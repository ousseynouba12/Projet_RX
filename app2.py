# machine1_employee_api.py
from flask import Flask, request, jsonify
import pymysql
import os
import secrets
import string
import hashlib
import base64
import time
import requests 

app = Flask(__name__)

# Database configuration for iRedMail
DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',  # Replace with your actual iRedMail DB user
    'password': 'passer',  # Replace with your actual password
    'db': 'vmail',
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor
}

def hash_password_ssha512(password):
    """Hash password using SSHA512 (Salted SHA-512) as used by iRedMail."""
    salt = os.urandom(8)  # 8 bytes of random salt
    sha = hashlib.sha512(password.encode() + salt).digest()
    return "{SSHA512}" + base64.b64encode(sha + salt).decode()

def generate_password(length=12):
    """Generate a secure random password."""
    alphabet = string.ascii_letters + string.digits + string.punctuation
    return ''.join(secrets.choice(alphabet) for _ in range(length))

def create_email_account(username, domain, full_name, department=None, password=None):
    """Create a new email account in iRedMail."""
    if not password:
        password = generate_password()    
    try:
        connection = pymysql.connect(**DB_CONFIG)
        with connection.cursor() as cursor:
            # Hash password for storage (using iRedMail's method)
            hashed_password = hash_password_ssha512(password)
            
            # Default values based on your schema
            maildir = f"{domain}/{username}/"
            
            # Insert into mailbox table
            sql = """
            INSERT INTO mailbox (
                username, password, name, language, mailboxformat, mailboxfolder,
                storagebasedirectory, storagenode, maildir, quota, domain, transport,
                department, rank, employeeid, active
            ) VALUES (
                %s, %s, %s, 'en_US', 'maildir', 'Maildir',
                '/var/vmail', 'vmail1', %s, 2048, %s, 'dovecot',
                %s, 'normal', %s, 1
            )
            """
            cursor.execute(sql, (
                f"{username}@{domain}", 
                hashed_password, 
                full_name, 
                maildir, 
                domain, 
                department, # Ajout du département
                username
            ))
            
            # Also add to the domain_admins table if needed
            # cursor.execute("INSERT INTO domain_admins (username, domain, created) VALUES (%s, 'ALL', NOW())", (email_address,))
            
            connection.commit()
            return True, password
            
    except Exception as e:
        print(f"Error creating email account: {e}")
        return False, str(e)
    finally:
        connection.close()

# Modification de la fonction get_employee
def get_employee(username, domain="smarttech.sn"):
    """Récupérer les informations d'un employé par son username et domaine."""
    try:
        connection = pymysql.connect(**DB_CONFIG)
        with connection.cursor() as cursor:
            sql = """
            SELECT username, name as full_name, domain, active, created, department
            FROM mailbox 
            WHERE username = %s
            """
            cursor.execute(sql, (f"{username}@{domain}",))
            result = cursor.fetchone()
            
            if result:
                # Transformer les données pour l'API
                result['username'] = result['username'].split('@')[0]  # Extraire le username sans domaine
                return True, result
            else:
                return False, "Employé non trouvé"
    except Exception as e:
        print(f"Erreur lors de la récupération de l'employé: {e}")
        return False, str(e)
    finally:
        connection.close()

# Modification de la fonction update_employee
def update_employee(old_username, new_username, full_name, department=None, domain="smarttech.sn", active=1):
    """Mettre à jour les informations d'un employé."""
    try:
        connection = pymysql.connect(**DB_CONFIG)
        with connection.cursor() as cursor:
            # Vérifier si le nouvel username est déjà utilisé (si différent de l'ancien)
            if old_username != new_username:
                cursor.execute("SELECT COUNT(*) as count FROM mailbox WHERE username = %s", 
                             (f"{new_username}@{domain}",))
                if cursor.fetchone()['count'] > 0:
                    return False, "Ce nom d'utilisateur est déjà pris"
            
            # Récupérer les anciennes informations pour le maildir
            cursor.execute("SELECT maildir FROM mailbox WHERE username = %s", 
                         (f"{old_username}@{domain}",))
            old_maildir = cursor.fetchone()
            
            if not old_maildir:
                return False, "Employé non trouvé"
            
            # Nouveau maildir si le username change
            new_maildir = f"{domain}/{new_username}/"
            
            # Mettre à jour les informations
            if old_username == new_username:
                # Mise à jour sans changement de username
                sql = """
                UPDATE mailbox 
                SET name = %s, department = %s, active = %s
                WHERE username = %s
                """
                cursor.execute(sql, (full_name, department, active, f"{old_username}@{domain}"))
            else:
                # Mise à jour avec changement de username
                sql = """
                UPDATE mailbox 
                SET username = %s, name = %s, maildir = %s, department = %s, employeeid = %s, active = %s
                WHERE username = %s
                """
                cursor.execute(sql, (
                    f"{new_username}@{domain}", 
                    full_name, 
                    new_maildir,
                    department,
                    new_username, 
                    active,
                    f"{old_username}@{domain}"
                ))
            
            connection.commit()
            return True, "Employé mis à jour avec succès"
    except Exception as e:
        print(f"Erreur lors de la mise à jour de l'employé: {e}")
        return False, str(e)
    finally:
        connection.close()

def delete_employee(username, domain="smarttech.sn"):
    """Supprimer un compte d'employé."""
    try:
        connection = pymysql.connect(**DB_CONFIG)
        with connection.cursor() as cursor:
            # Vérifier si l'employé existe
            cursor.execute("SELECT COUNT(*) as count FROM mailbox WHERE username = %s", 
                         (f"{username}@{domain}",))
            if cursor.fetchone()['count'] == 0:
                return False, "Employé non trouvé"
            
            # Supprimer l'employé
            sql = "DELETE FROM mailbox WHERE username = %s"
            cursor.execute(sql, (f"{username}@{domain}",))
            
            connection.commit()
            return True, "Employé supprimé avec succès"
    except Exception as e:
        print(f"Erreur lors de la suppression de l'employé: {e}")
        return False, str(e)
    finally:
        connection.close()

# Routes API supplémentaires

@app.route('/api/employees/<username>', methods=['GET'])
def get_employee_route(username):
    """Route pour récupérer les informations d'un employé."""
    domain = request.args.get('domain', 'smarttech.sn')  # Domaine optionnel dans les query params
    
    success, result = get_employee(username, domain)
    
    if success:
        return jsonify({
            'status': 'success',
            'employee': result
        })
    else:
        return jsonify({
            'status': 'error',
            'message': result
        }), 404

@app.route('/api/employees', methods=['POST'])
def create_employee():
    data = request.json
    
    if not data or 'username' not in data or 'full_name' not in data:
        return jsonify({'error': 'Missing required fields'}), 400
    
    username = data['username']
    full_name = data['full_name']
    department = data.get('department', '')  # Département optionnel
    domain = data.get('domain', 'smarttech.sn')  # Default domain
    password = data.get('password', None)  # Optional password or generate one
    personal_email = data.get('personal_email')  # Email personnel
    
    success, result = create_email_account(username, domain, full_name, department, password)
    
    if success:
        # Notify Machine 2 to create FTP account
        try:
            ftp_api_url = "http://192.168.1.12:5000/api/ftp_users"
            ftp_data = {
                'username': username,
                'email': f"{username}@{domain}",
                'password': result,  # Pass the generated password
                'personal_email': personal_email,
                'department': department  # Transmettre le département
            }
            response = requests.post(ftp_api_url, json=ftp_data)
            ftp_status = response.json()
        except Exception as e:
            ftp_status = {'error': str(e)}
        
        return jsonify({
            'status': 'success',
            'message': 'Employee created successfully',
            'email': f"{username}@{domain}",
            'password': result,
            'department': department,
            'ftp_account_status': ftp_status
        })
    else:
        return jsonify({
            'status': 'error',
            'message': f'Failed to create employee: {result}'
        }), 500

@app.route('/api/employees/<old_username>', methods=['PUT'])
def update_employee_route(old_username):
    """Route pour mettre à jour les informations d'un employé."""
    data = request.json
    
    if not data or 'new_username' not in data or 'full_name' not in data:
        return jsonify({'error': 'Champs requis manquants'}), 400
    
    new_username = data['new_username']
    full_name = data['full_name']
    department = data.get('department', '')
    domain = data.get('domain', 'smarttech.sn')
    active = data.get('active', 1)
    
    success, result = update_employee(old_username, new_username, full_name, department, domain, active)
    
    if success:
        # Si le username a changé, mettre à jour le compte FTP également
        ftp_update_status = "Non modifié"
        try:
            ftp_api_url = "http://192.168.1.12:5000/api/ftp_users/update"
            ftp_data = {
                'old_username': old_username,
                'new_username': new_username,
                'email': f"{new_username}@{domain}",
                'department': department  # Transmettre le département mis à jour
            }
            response = requests.put(ftp_api_url, json=ftp_data)
            ftp_update_status = response.json()
        except Exception as e:
            ftp_update_status = {'error': str(e)}
        
        return jsonify({
            'status': 'success',
            'message': result,
            'ftp_account_status': ftp_update_status
        })
    else:
        return jsonify({
            'status': 'error',
            'message': result
        }), 500

@app.route('/api/employees/<username>', methods=['DELETE'])
def delete_employee_route(username):
    """Route pour supprimer un employé."""
    domain = request.args.get('domain', 'smarttech.sn')
    
    success, result = delete_employee(username, domain)
    
    if success:
        # Supprimer également le compte FTP
        try:
            ftp_api_url = f"http://192.168.1.12:5000/api/ftp_users/{username}"
            response = requests.delete(ftp_api_url)
            ftp_status = response.json()
        except Exception as e:
            ftp_status = {'error': str(e)}
        
        return jsonify({
            'status': 'success',
            'message': result,
            'ftp_account_status': ftp_status
        })
    else:
        return jsonify({
            'status': 'error',
            'message': result
        }), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)