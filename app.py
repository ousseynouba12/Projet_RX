from flask import Flask, request, jsonify
import os
import subprocess
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import time
import threading
import re
import pymysql
import datetime
import json

app = Flask(__name__)

# Configuration
FTP_USERLIST_FILE = '/etc/vsftpd.userlist'
FTP_LOG_FILE = '/var/log/vsftpd.log'
FTP_GROUP = 'ftpgroup'
ADMIN_EMAIL = 'ousseynou@smarttech.sn'
SMTP_SERVER = '192.168.1.16'
SMTP_PORT = 587
DOMAIN = 'smarttech.sn'

# Configuration de la base de données
DB_CONFIG = {
    'host': '192.168.1.12',
    'user': 'ousseynou',
    'password': 'passer',
    'db': 'ftpmanager',
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor
}


def get_db_connection():
    """Établir une connexion à la base de données."""
    return pymysql.connect(**DB_CONFIG)


def log_ftp_event(
        username,
        event_type,
        client_ip,
        file_path=None,
        file_size=None,
        status='success',
        details=None):
    """Enregistrer un événement FTP dans la base de données."""
    try:
        connection = get_db_connection()
        with connection.cursor() as cursor:
            # Récupérer l'ID de l'utilisateur
            cursor.execute(
                "SELECT id FROM ftp_users WHERE username = %s", (username,))
            user_result = cursor.fetchone()
            user_id = user_result['id'] if user_result else None

            # Récupérer l'ID du type d'événement
            cursor.execute(
                "SELECT id FROM event_types WHERE name = %s", (event_type,))
            event_type_result = cursor.fetchone()
            event_type_id = event_type_result['id'] if event_type_result else None

            if not event_type_id:
                # Si le type d'événement n'existe pas, l'ajouter
                cursor.execute(
                    "INSERT INTO event_types (name) VALUES (%s)", (event_type,))
                event_type_id = cursor.lastrowid

            # Insérer l'événement
            sql = """
            INSERT INTO ftp_events (
                ftp_user_id, event_type_id, client_ip, file_path, file_size, status, details
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s
            )
            """
            cursor.execute(sql, (
                user_id,
                event_type_id,
                client_ip,
                file_path,
                file_size,
                status,
                details
            ))

            connection.commit()
            
            # Vérifier si c'est un événement de connexion ou d'action FTP pour envoyer un email
            if event_type in ['login', 'upload', 'download'] and username != 'system':
                # Récupérer les informations de l'utilisateur
                cursor.execute(
                    "SELECT email FROM ftp_users WHERE id = %s", (user_id,))
                user_info = cursor.fetchone()
                
                if user_info and user_info['email']:
                    # Envoyer un email à l'administrateur uniquement
                    notify_admin_about_ftp_activity(username, event_type, client_ip, file_path, file_size)
            
            return True
    except Exception as e:
        print(f"Erreur lors de l'enregistrement de l'événement: {e}")
        return False
    finally:
        connection.close()


def notify_admin_about_ftp_activity(username, event_type, client_ip, file_path=None, file_size=None):
    """Envoie une notification à l'administrateur concernant l'activité FTP."""
    try:
        # Définir les identifiants SMTP
        smtp_user = "ftp_system@smarttech.sn"
        smtp_password = "N$pad$9626"
        
        # Préparer le contenu de l'email
        action_text = {
            'login': 's\'est connecté',
            'upload': 'a uploadé un fichier',
            'download': 'a téléchargé un fichier'
        }
        
        subject = f"Activité FTP: {username} {action_text.get(event_type, 'a effectué une action')}"
        
        body = f"""
Activité FTP détectée:

Utilisateur: {username}
Action: {action_text.get(event_type, event_type)}
IP Client: {client_ip}
Date/Heure: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""

        if file_path:
            body += f"Fichier: {file_path}\n"
            
        if file_size:
            # Convertir la taille en format lisible
            def format_bytes(size):
                power = 1024
                n = 0
                power_labels = {0: 'B', 1: 'KB', 2: 'MB', 3: 'GB', 4: 'TB'}
                while size > power:
                    size /= power
                    n += 1
                return f"{size:.2f} {power_labels.get(n, '')}"
            
            body += f"Taille: {format_bytes(file_size)}\n"
            
        msg = MIMEMultipart()
        msg['From'] = smtp_user
        msg['To'] = ADMIN_EMAIL
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        # Envoyer l'email
        with smtplib.SMTP_SSL(SMTP_SERVER, 465) as server:
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, ADMIN_EMAIL, msg.as_string())
            print(f"Notification d'activité FTP envoyée à {ADMIN_EMAIL}")
            
        return True
    except Exception as e:
        print(f"Erreur d'envoi d'email: {e}")
        import traceback
        traceback.print_exc()
        return False

def create_ftp_user(username, password, email, department=None):
    """Create a system user for FTP access and save to database."""
    try:
        # Create user with home directory
        home_dir = f'/home/{username}'
        subprocess.run(['useradd', '-m', '-d', home_dir, '-g',
                       FTP_GROUP, '-s', '/bin/bash', username], check=True)

        # Set password
        proc = subprocess.Popen(['passwd',
                                 username],
                                stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        proc.communicate(input=f"{password}\n{password}\n".encode())

        # Add to vsftpd.userlist
        with open(FTP_USERLIST_FILE, 'a') as f:
            f.write(f"{username}\n")

        # Set proper permissions
        os.makedirs(f"{home_dir}/ftp", exist_ok=True)
        os.chown(f"{home_dir}/ftp",
                 int(subprocess.check_output(['id',
                                              '-u',
                                              username]).strip()),
                 int(subprocess.check_output(['id',
                                              '-g',
                                              username]).strip()))
        os.chmod(f"{home_dir}/ftp", 0o755)

        # Ajouter l'utilisateur à la base de données
        connection = get_db_connection()
        try:
            with connection.cursor() as cursor:
                # Vérifier si l'utilisateur existe déjà
                cursor.execute(
                    "SELECT id FROM ftp_users WHERE username = %s", (username,))
                existing_user = cursor.fetchone()

                if existing_user:
                    # Mettre à jour l'utilisateur existant
                    sql = """
                    UPDATE ftp_users
                    SET email = %s, department = %s, home_directory = %s, is_active = TRUE
                    WHERE username = %s
                    """
                    cursor.execute(
                        sql, (email, department, home_dir, username))
                else:
                    # Insérer un nouvel utilisateur
                    sql = """
                    INSERT INTO ftp_users (username, email, department, home_directory, is_active)
                    VALUES (%s, %s, %s, %s, TRUE)
                    """
                    cursor.execute(
                        sql, (username, email, department, home_dir))

                connection.commit()
        finally:
            connection.close()

        # Enregistrer l'événement de création d'utilisateur
        log_ftp_event(
            username,
            'user_created',
            'localhost',
            None,
            None,
            'success',
            json.dumps({
                'email': email,
                'department': department,
                'home_directory': home_dir
            })
        )

        return True, "User created successfully"
    except Exception as e:
        # Enregistrer l'échec de création d'utilisateur
        log_ftp_event(
            'system',
            'user_creation_failed',
            'localhost',
            None,
            None,
            'error',
            json.dumps({
                'username': username,
                'email': email,
                'department': department,
                'error': str(e)
            })
        )
        return False, str(e)


def update_ftp_user(old_username, new_username, email=None, department=None):
    """Mettre à jour un utilisateur FTP existant."""
    try:
        # Vérifier si l'ancien utilisateur existe
        if not os.path.exists(f'/home/{old_username}'):
            return False, f"L'utilisateur FTP {old_username} n'existe pas"

        # Vérifier si le nouvel utilisateur existe déjà
        if old_username != new_username and os.path.exists(
                f'/home/{new_username}'):
            return False, f"Le nom d'utilisateur {new_username} est déjà utilisé"

        # Si le nom d'utilisateur a changé
        if old_username != new_username:
            # Créer un nouvel utilisateur
            subprocess.run(['usermod', '-l', new_username,
                           old_username], check=True)
            subprocess.run(
                ['usermod', '-d', f'/home/{new_username}', '-m', new_username], check=True)

            # Mettre à jour le fichier vsftpd.userlist
            with open(FTP_USERLIST_FILE, 'r') as f:
                users = f.read().splitlines()

            if old_username in users:
                users.remove(old_username)
                users.append(new_username)

                with open(FTP_USERLIST_FILE, 'w') as f:
                    for user in users:
                        f.write(f"{user}\n")

        # Mettre à jour la base de données
        connection = get_db_connection()
        try:
            with connection.cursor() as cursor:
                # Récupérer l'ID de l'utilisateur
                cursor.execute(
                    "SELECT id FROM ftp_users WHERE username = %s", (old_username,))
                user_result = cursor.fetchone()

                if user_result:
                    user_id = user_result['id']

                    # Mettre à jour l'utilisateur
                    sql = """
                    UPDATE ftp_users
                    SET username = %s, email = %s, department = %s, home_directory = %s
                    WHERE id = %s
                    """
                    cursor.execute(sql, (
                        new_username,
                        email,
                        department,
                        f'/home/{new_username}',
                        user_id
                    ))

                    connection.commit()
                else:
                    # L'utilisateur n'existe pas dans la BD, l'ajouter
                    sql = """
                    INSERT INTO ftp_users (username, email, department, home_directory, is_active)
                    VALUES (%s, %s, %s, %s, TRUE)
                    """
                    cursor.execute(sql, (
                        new_username,
                        email,
                        department,
                        f'/home/{new_username}'
                    ))

                    connection.commit()
        finally:
            connection.close()

        # Enregistrer l'événement de mise à jour d'utilisateur
        log_ftp_event(
            new_username,
            'user_updated',
            'localhost',
            None,
            None,
            'success',
            json.dumps({
                'old_username': old_username,
                'new_username': new_username,
                'email': email,
                'department': department
            })
        )

        return True, "Utilisateur FTP mis à jour avec succès"
    except Exception as e:
        # Enregistrer l'échec de mise à jour d'utilisateur
        log_ftp_event(
            old_username,
            'user_update_failed',
            'localhost',
            None,
            None,
            'error',
            json.dumps({
                'old_username': old_username,
                'new_username': new_username,
                'email': email,
                'department': department,
                'error': str(e)
            })
        )
        return False, str(e)

def delete_ftp_user(username):
    """Supprimer un utilisateur FTP."""
    try:
        # Récupérer les informations avant suppression pour l'enregistrement
        connection = get_db_connection()
        user_info = None
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM ftp_users WHERE username = %s", (username,))
                user_info = cursor.fetchone()
        finally:
            connection.close()

        # Vérifier si l'utilisateur existe
        if not os.path.exists(f'/home/{username}'):
            return False, f"L'utilisateur FTP {username} n'existe pas"

        # Supprimer l'utilisateur du système
        subprocess.run(['userdel', '-r', username], check=True)

        # Supprimer du fichier vsftpd.userlist
        with open(FTP_USERLIST_FILE, 'r') as f:
            users = f.read().splitlines()

        if username in users:
            users.remove(username)

            with open(FTP_USERLIST_FILE, 'w') as f:
                for user in users:
                    f.write(f"{user}\n")

        # Désactiver l'utilisateur dans la base de données
        connection = get_db_connection()
        try:
            with connection.cursor() as cursor:
                # Désactiver plutôt que supprimer pour conserver l'historique
                sql = "UPDATE ftp_users SET is_active = FALSE WHERE username = %s"
                cursor.execute(sql, (username,))
                connection.commit()

                # Enregistrer l'événement de suppression d'utilisateur
                log_ftp_event(
                    'system',
                    'user_deleted',
                    'localhost',
                    None,
                    None,
                    'success',
                    json.dumps({
                        'username': username,
                        'email': user_info['email'] if user_info else None,
                        'department': user_info['department'] if user_info else None
                    })
                )

                return True, "Utilisateur FTP supprimé avec succès"
        except Exception as e:
            # Enregistrer l'échec de suppression d'utilisateur
            log_ftp_event(
                'system',
                'user_deletion_failed',
                'localhost',
                None,
                None,
                'error',
                json.dumps({
                    'username': username,
                    'error': str(e)
                })
            )
            return False, str(e)
        finally:
            connection.close()
    except Exception as e:
        # Enregistrer l'échec de suppression d'utilisateur
        log_ftp_event(
            'system',
            'user_deletion_failed',
            'localhost',
            None,
            None,
            'error',
            json.dumps({
                'username': username,
                'error': str(e)
            })
        )
        return False, str(e)



# API route to create FTP users
@app.route('/api/ftp_users', methods=['POST'])
def create_ftp_user_endpoint():
    """API endpoint pour créer un utilisateur FTP."""
    data = request.json

    if not data or 'username' not in data or 'email' not in data:
        return jsonify({'error': 'Champs requis manquants'}), 400

    username = data['username']
    email = data['email']
    password = data.get('password')
    department = data.get('department', '')  # Récupération du département

    # Si aucun mot de passe n'est fourni, générer un mot de passe aléatoire
    if not password:
        import random
        import string
        password = ''.join(
            random.choice(
                string.ascii_letters +
                string.digits) for _ in range(12))

    success, result = create_ftp_user(username, password, email, department)

    if success:
        return jsonify({
            'status': 'success',
            'message': 'FTP account created successfully',
            'username': username,
            'email': email,
            'department': department,
            'password': password  # Renvoyer le mot de passe pour information
        })
    else:
        return jsonify({
            'status': 'error',
            'message': f'Failed to create FTP account: {result}'
        }), 500


@app.route('/api/ftp_users/update', methods=['PUT'])
def update_ftp_user_endpoint():
    """API endpoint pour mettre à jour un utilisateur FTP."""
    data = request.json

    if not data or 'old_username' not in data or 'new_username' not in data:
        return jsonify({'error': 'Champs requis manquants'}), 400

    old_username = data['old_username']
    new_username = data['new_username']
    email = data.get('email')
    department = data.get('department', '')  # Récupération du département

    success, result = update_ftp_user(
        old_username, new_username, email, department)

    if success:
        return jsonify({
            'status': 'success',
            'message': result,
            'username': new_username,
            'email': email,
            'department': department
        })
    else:
        return jsonify({
            'status': 'error',
            'message': result
        }), 500


@app.route('/api/ftp_users/<username>', methods=['DELETE'])
def delete_ftp_user_endpoint(username):
    """API endpoint pour supprimer un utilisateur FTP."""
    success, result = delete_ftp_user(username)

    if success:
        return jsonify({
            'status': 'success',
            'message': result
        })
    else:
        return jsonify({
            'status': 'error',
            'message': result
        }), 500


@app.route('/api/ftp_users', methods=['GET'])
def get_all_ftp_users():
    """Récupérer tous les utilisateurs FTP actifs."""
    try:
        connection = get_db_connection()
        with connection.cursor() as cursor:
            # Récupérer les utilisateurs actifs
            sql = """
            SELECT id, username, email, department, home_directory, quota, created_at, last_modified
            FROM ftp_users
            WHERE is_active = TRUE
            ORDER BY username
            """
            cursor.execute(sql)
            users = cursor.fetchall()

            # Récupérer les statistiques des utilisateurs
            for user in users:
                # Conversion des dates en chaînes pour la sérialisation JSON
                user['created_at'] = user['created_at'].strftime(
                    '%Y-%m-%d %H:%M:%S')
                user['last_modified'] = user['last_modified'].strftime(
                    '%Y-%m-%d %H:%M:%S')

                # Récupérer les statistiques d'utilisation
                cursor.execute("""
                SELECT
                    COUNT(CASE WHEN et.name = 'upload' THEN 1 END) AS uploads,
                    COUNT(CASE WHEN et.name = 'download' THEN 1 END) AS downloads,
                    COUNT(CASE WHEN et.name = 'login' THEN 1 END) AS logins,
                    MAX(fe.timestamp) AS last_activity
                FROM ftp_events fe
                JOIN event_types et ON fe.event_type_id = et.id
                WHERE fe.ftp_user_id = %s
                """, (user['id'],))

                stats = cursor.fetchone()
                if stats:
                    user['stats'] = stats
                    if stats['last_activity']:
                        user['stats']['last_activity'] = stats['last_activity'].strftime(
                            '%Y-%m-%d %H:%M:%S')

            return jsonify({
                'status': 'success',
                'users': users
            })

    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Erreur lors de la récupération des utilisateurs FTP: {str(e)}'
        }), 500
    finally:
        connection.close()


@app.route('/api/ftp_users/<username>', methods=['GET'])
def get_ftp_user(username):
    """Récupérer les informations d'un utilisateur FTP spécifique."""
    try:
        connection = get_db_connection()
        with connection.cursor() as cursor:
            # Récupérer l'utilisateur
            sql = """
            SELECT id, username, email, department, home_directory, quota,
                   created_at, last_modified, is_active
            FROM ftp_users
            WHERE username = %s
            """
            cursor.execute(sql, (username,))
            user = cursor.fetchone()

            if not user:
                return jsonify({
                    'status': 'error',
                    'message': f'Utilisateur FTP {username} non trouvé'
                }), 404

            # Conversion des dates en chaînes pour la sérialisation JSON
            user['created_at'] = user['created_at'].strftime(
                '%Y-%m-%d %H:%M:%S')
            user['last_modified'] = user['last_modified'].strftime(
                '%Y-%m-%d %H:%M:%S')

            # Récupérer les événements récents de l'utilisateur (tous sans pagination)
            cursor.execute("""
            SELECT fe.id, fe.timestamp, et.name as event_type, fe.client_ip,
                   fe.file_path, fe.file_size, fe.status, fe.details
            FROM ftp_events fe
            JOIN event_types et ON fe.event_type_id = et.id
            WHERE fe.ftp_user_id = %s
            ORDER BY fe.timestamp DESC
            """, (user['id'],))

            events = cursor.fetchall()
            for event in events:
                event['timestamp'] = event['timestamp'].strftime(
                    '%Y-%m-%d %H:%M:%S')
                # Convertir JSON stocké en string en dictionnaire si nécessaire
                if event['details'] and isinstance(event['details'], str):
                    try:
                        event['details'] = json.loads(event['details'])
                    except BaseException:
                        pass  # Garder en tant que string si pas JSON valide

            user['events'] = events

            # Récupérer les statistiques générales
            cursor.execute("""
            SELECT
                COUNT(CASE WHEN et.name = 'upload' THEN 1 END) AS uploads,
                COUNT(CASE WHEN et.name = 'download' THEN 1 END) AS downloads,
                COUNT(CASE WHEN et.name = 'login' THEN 1 END) AS logins,
                COUNT(CASE WHEN et.name = 'login_failed' THEN 1 END) AS failed_logins,
                MAX(fe.timestamp) AS last_activity,
                SUM(CASE WHEN et.name = 'upload' THEN fe.file_size ELSE 0 END) AS total_uploaded_bytes,
                SUM(CASE WHEN et.name = 'download' THEN fe.file_size ELSE 0 END) AS total_downloaded_bytes
            FROM ftp_events fe
            JOIN event_types et ON fe.event_type_id = et.id
            WHERE fe.ftp_user_id = %s
            """, (user['id'],))

            stats = cursor.fetchone()
            if stats:
                user['stats'] = stats
                if stats['last_activity']:
                    user['stats']['last_activity'] = stats['last_activity'].strftime(
                        '%Y-%m-%d %H:%M:%S')

                # Convertir les octets en format lisible
                if stats['total_uploaded_bytes']:
                    user['stats']['total_uploaded'] = format_bytes(
                        stats['total_uploaded_bytes'])
                if stats['total_downloaded_bytes']:
                    user['stats']['total_downloaded'] = format_bytes(
                        stats['total_downloaded_bytes'])

            return jsonify({
                'status': 'success',
                'user': user
            })

    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Erreur lors de la récupération des informations de l\'utilisateur: {str(e)}'
        }), 500
    finally:
        connection.close()


def format_bytes(size):
    """Convertir les octets en format lisible (KB, MB, GB, etc.)."""
    power = 1024
    n = 0
    power_labels = {0: 'B', 1: 'KB', 2: 'MB', 3: 'GB', 4: 'TB'}
    while size > power:
        size /= power
        n += 1
    return f"{size:.2f} {power_labels.get(n, '')}"


@app.route('/api/events', methods=['GET'])
def get_events():
    """Récupérer tous les événements FTP avec filtres (sans pagination)."""
    try:
        # Filtres
        username = request.args.get('username')
        event_type = request.args.get('event_type')
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')

        connection = get_db_connection()
        with connection.cursor() as cursor:
            # Construction de la requête SQL avec filtres
            sql = """
            SELECT
                fe.id, fe.timestamp, fu.username, fu.department,
                et.name as event_type, fe.client_ip, fe.file_path,
                fe.file_size, fe.status, fe.details
            FROM ftp_events fe
            LEFT JOIN ftp_users fu ON fe.ftp_user_id = fu.id
            JOIN event_types et ON fe.event_type_id = et.id
            WHERE 1=1
            """
            params = []

            # Ajouter les filtres si spécifiés
            if username:
                sql += " AND fu.username = %s"
                params.append(username)

            if event_type:
                sql += " AND et.name = %s"
                params.append(event_type)

            if start_date:
                sql += " AND fe.timestamp >= %s"
                params.append(start_date)

            if end_date:
                sql += " AND fe.timestamp <= %s"
                params.append(end_date)

            # Ajouter l'ordre sans pagination
            sql += " ORDER BY fe.timestamp DESC"

            cursor.execute(sql, params)
            events = cursor.fetchall()

            # Formater les données
            for event in events:
                event['timestamp'] = event['timestamp'].strftime(
                    '%Y-%m-%d %H:%M:%S')
                # Convertir JSON stocké en string en dictionnaire
                if event['details'] and isinstance(event['details'], str):
                    try:
                        event['details'] = json.loads(event['details'])
                    except BaseException:
                        pass

            return jsonify({
                'status': 'success',
                'events': events
            })

    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Erreur lors de la récupération des événements: {str(e)}'
        }), 500
    finally:
        connection.close()


@app.route('/api/departments', methods=['GET'])
def get_departments():
    """Récupérer la liste des départements utilisés."""
    try:
        connection = get_db_connection()
        with connection.cursor() as cursor:
            cursor.execute("""
            SELECT DISTINCT department
            FROM ftp_users
            WHERE department IS NOT NULL AND department != ''
            ORDER BY department
            """)

            departments = [row['department'] for row in cursor.fetchall()]

            return jsonify({
                'status': 'success',
                'departments': departments
            })

    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Erreur lors de la récupération des départements: {str(e)}'
        }), 500
    finally:
        connection.close()




@app.route('/api/stats/usage_by_department', methods=['GET'])
def get_usage_by_department():
    """Récupérer les statistiques d'utilisation par département."""
    try:
        connection = get_db_connection()
        with connection.cursor() as cursor:
            cursor.execute("""
            SELECT
                COALESCE(fu.department, 'Non spécifié') as department,
                COUNT(DISTINCT fu.id) as user_count,
                COUNT(fe.id) as event_count,
                SUM(CASE WHEN et.name = 'upload' THEN 1 ELSE 0 END) as uploads,
                SUM(CASE WHEN et.name = 'download' THEN 1 ELSE 0 END) as downloads,
                SUM(CASE WHEN et.name = 'upload' THEN fe.file_size ELSE 0 END) as uploaded_bytes,
                SUM(CASE WHEN et.name = 'download' THEN fe.file_size ELSE 0 END) as downloaded_bytes
            FROM ftp_users fu
            LEFT JOIN ftp_events fe ON fu.id = fe.ftp_user_id
            LEFT JOIN event_types et ON fe.event_type_id = et.id
            WHERE fu.is_active = TRUE
            GROUP BY fu.department
            ORDER BY user_count DESC
            """)

            results = cursor.fetchall()

            # Formater les tailles de fichiers
            for row in results:
                if row['uploaded_bytes']:
                    row['uploaded_formatted'] = format_bytes(
                        row['uploaded_bytes'])
                if row['downloaded_bytes']:
                    row['downloaded_formatted'] = format_bytes(
                        row['downloaded_bytes'])

            return jsonify({
                'status': 'success',
                'department_stats': results
            })

    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Erreur lors de la récupération des statistiques par département: {str(e)}'
        }), 500
    finally:
        connection.close()


@app.route('/api/stats/top_users', methods=['GET'])
def get_top_users():
    """Récupérer les utilisateurs les plus actifs."""
    try:
        limit = int(request.args.get('limit', 10))
        period = request.args.get('period', 'month')  # day, week, month, all

        connection = get_db_connection()
        with connection.cursor() as cursor:
            # Déterminer la condition de date en fonction de la période
            date_condition = ""
            if period == 'day':
                date_condition = "AND fe.timestamp >= DATE_SUB(NOW(), INTERVAL 1 DAY)"
            elif period == 'week':
                date_condition = "AND fe.timestamp >= DATE_SUB(NOW(), INTERVAL 1 WEEK)"
            elif period == 'month':
                date_condition = "AND fe.timestamp >= DATE_SUB(NOW(), INTERVAL 1 MONTH)"

            cursor.execute(f"""
            SELECT
                fu.username,
                fu.department,
                COUNT(fe.id) as event_count,
                SUM(CASE WHEN et.name = 'upload' THEN 1 ELSE 0 END) as uploads,
                SUM(CASE WHEN et.name = 'download' THEN 1 ELSE 0 END) as downloads,
                SUM(CASE WHEN et.name = 'upload' THEN fe.file_size ELSE 0 END) as uploaded_bytes,
                SUM(CASE WHEN et.name = 'download' THEN fe.file_size ELSE 0 END) as downloaded_bytes,
                MAX(fe.timestamp) as last_activity
            FROM ftp_users fu
            JOIN ftp_events fe ON fu.id = fe.ftp_user_id
            JOIN event_types et ON fe.event_type_id = et.id
            WHERE fu.is_active = TRUE {date_condition}
            GROUP BY fu.id, fu.username, fu.department
            ORDER BY event_count DESC
            LIMIT %s
            """, (limit,))

            results = cursor.fetchall()

            # Formater les résultats
            for row in results:
                if row['uploaded_bytes']:
                    row['uploaded_formatted'] = format_bytes(
                        row['uploaded_bytes'])
                if row['downloaded_bytes']:
                    row['downloaded_formatted'] = format_bytes(
                        row['downloaded_bytes'])
                if row['last_activity']:
                    row['last_activity'] = row['last_activity'].strftime(
                        '%Y-%m-%d %H:%M:%S')

            return jsonify({
                'status': 'success',
                'period': period,
                'top_users': results
            })

    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Erreur lors de la récupération des utilisateurs les plus actifs: {str(e)}'
        }), 500
    finally:
        connection.close()


# Démarrer l'application Flask
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)

