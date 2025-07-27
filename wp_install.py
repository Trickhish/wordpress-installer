#!/usr/bin/env python3

import argparse
import string
import secrets
import requests
import mysql.connector
from mysql.connector import Error
import platform
import subprocess
import unicodedata
import os
import re
import zipfile
import shutil
from datetime import datetime

WP_DL_LINK = "https://wordpress.org/latest.zip"

WP_CONFIG_SAMPLE = '''<?php
// ** Database settings - You can get this info from your web host ** //
/** Nom de la base de données de WordPress. */
define('DB_NAME', '');

/** Utilisateur de la base de données MySQL. */
define('DB_USER', '');

/** Mot de passe de la base de données MySQL. */
define('DB_PASSWORD', '');

/** Adresse de l'hébergement MySQL. */
define('DB_HOST', 'localhost');

/** Jeu de caractères à utiliser par la base de données lors de la création des tables. */
define('DB_CHARSET', 'utf8');

/** Type de collation de la base de données. */
define('DB_COLLATE', '');

/**
 * Préfixe de base de données pour les tables de WordPress.
 */
$table_prefix = 'wp_';

/**
 * Pour les développeurs : le mode débogage de WordPress.
 */
define('WP_DEBUG', false);

/* C'est tout, ne touchez pas à ce qui suit ! Bonne publication. */

/** Chemin absolu vers le dossier de WordPress. */
if ( ! defined( 'ABSPATH' ) ) {
    define( 'ABSPATH', __DIR__ . '/' );
}

/** Réglage des variables de WordPress et de ses fichiers inclus. */
require_once ABSPATH . 'wp-settings.php';
'''

class Colors:
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    BLUE = '\033[0;34m'
    NC = '\033[0m'  # No Color

def ping(host):
    param = '-n' if platform.system().lower()=='windows' else '-c'

    command = ['ping', param, '1', host]

    return subprocess.call(command) == 0

def log(msg, logtype="info"):
    tps = {
        "info": ["INFO", Colors.BLUE],
        "success": ["SUCC", Color.GREEN],
        "warning": ["WARN", Color.YELLOW],
        "error": ["ERR", Color.RED],
    }
    if not logtype in tps:
        logtype="info"
    
    tt,cl = tps[logtype]

    print(f"{cl}[{tt}]{Colors.NC} {msg}")

def askBool(msg, default=None):
    yas = ["yes", "y", "oui", "o"]
    nas = ["no", "n", "non"]

    while True:
        r = str(input(f"{msg} [{'Y' if default==True else 'y'}/{'N' if default==False else 'n'}]: ")).lower()
        if default!=None and r.strip()=="":
            return(default)
        elif r in yas:
            return(True)
        elif r in nas:
            return(True)

def askText(msg, default=None):
    while True:
        r = str(input(f"{msg}{f' [{default}]' if default!=None else ''}: "))
        if r.strip()=="":
            if default!=None:
                return(default)
        else:
            return(r.strip())

def askChoice(msg, choices, default=None):
    if choices==[]:
        return
    
    print(msg)
    for i,ch in enumerate(choices):
        print(f" {'->' if i==default else '  '} [{i}] {ch}")
    
    while True:
        r = str(input(f"Choice{' [0]' if default!=None else ''}: "))
        if r.strip()=="" and default!=None:
            return(default)
        elif r.isdigit() and int(r)>=0 and int(r)<len(choices):
            return(int(r))


def genPassword(length=40):
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return ''.join(secrets.choice(alphabet) for _ in range(length))

def formatName(name):
    name = name.lower()
    name = name.replace(" ", "_")

    name = unicodedata.normalize('NFD', name)
    name = ''.join(c for c in name if unicodedata.category(c) != 'Mn')

    return(name)

def runCommand(command, check=True, shell=True):
    try:
        result = subprocess.run(
            command, 
            shell=shell, 
            check=check, 
            capture_output=True, 
            text=True
        )
        return result
    except subprocess.CalledProcessError as e:
        return(e.stderr)
        #print(f"Erreur lors de l'exécution de la commande: {command}")
        #print(f"Code de retour: {e.returncode}")
        #print(f"Erreur: {e.stderr}")
        #raise

ERROR_PATTERNS = [
    r'ERROR\s+(\d+)\s*\(([^)]+)\):\s*(.*)', 
    r'ERROR\s+(\d+)\s*\(([A-Z0-9]+)\):\s*(.*)',
    r'ERROR\s+(\d+):\s*(.*)',
    r'Error\s+Code:\s*(\d+)\.\s*(.*)',
    r'mysql:\s*\[ERROR\]\s*(\d+):\s*(.*)',
    r'ERROR\s+(\d+)\s*\(([A-Z0-9]+)\)\s+at\s+line\s+\d+:\s*(.*)',
    r'ERROR\s+(\d+)\s*\(([^)]+)\):\s*(.*)',
    r'ERROR\s+(\d+)\s*\(([A-Z0-9]+)\)\s+([^:].*)'
]

def mysqlExtractStatus(error_message):
    if not error_message:
        return None
    
    for pattern in ERROR_PATTERNS:
        match = re.search(pattern, error_message, re.IGNORECASE)
        if match:
            groups = match.groups()
            
            if len(groups) == 3:
                error_code = int(groups[0])
                sqlstate = groups[1]
                message = groups[2].strip()
            elif len(groups) == 2:
                error_code = int(groups[0])
                sqlstate = None
                message = groups[1].strip()
            else:
                continue
            
            return {
                'error_code': error_code,
                'sqlstate': sqlstate,
                'message': message
            }
    
    return None

def runMysql(cmd, raiseOnError=False):
    try:
        result = subprocess.run(
            f"sudo mysql -e \"{cmd}\"", 
            shell=True,
            check=True, 
            capture_output=True, 
            text=True
        )
        return(True, result.stdout, result.returncode)
    except subprocess.CalledProcessError as e:
        err = mysqlExtractStatus(e.stderr)
        if raiseOnError:
            print(f"MYSQL: error {err['error_code']} : {err['message']}")
            raise
        return(False, e.stderr, err)

def listDb():
    r = runCommand(f"sudo mysql -e \"show databases;\"").stdout
    r = r.strip().split("\n")
    r = [x.strip().lower() for x in r if x.strip() not in ["Database", "information_schema", "performance_schema", "mysql", "sys"]]
    return(r)

def createUser(db_user):
    db_pass = genPassword(45)

    print(f"sudo mysql -e \"create user '{db_user}'@'localhost' identified by '{db_pass}';\"")

    r = runCommand(f"sudo mysql -e \"create user '{db_user}'@'localhost' identified by '{db_pass}';\"")
    if r.returncode!=0:
        print(r)
        exit()

    print(f"    Created user '{db_user}' with password '{db_pass}'")
    
    return(db_pass)

def createDb(db_name, db_user):
    succ,out,err = runMysql(f"create database {db_name};")
    if not succ:
        if err!=None:
            code = err["error_code"]
            msg = err["message"]
            if code==1007:
                print(f"The database already exists")
            else:
                print(f"MYSQL: error {code} : {msg}")
        exit()


    print(f"    Created database '{db_name}'")
    
    db_pass = createUser(db_user)

    succ,out,err = runMysql(f"grant all privileges on {db_name}.* to '{db_user}'@'localhost';")
    if not succ:
        if err!=None:
            code = err["error_code"]
            msg = err["message"]
            print(f"MYSQL: error {code} : {msg}")
        exit()

    print(f"    Granted all privileges on '{db_name}' to user '{db_user}'")

    return(db_pass)

def checkDbConnection(db_host, db_user, db_pass, db_name=None):
    if not ping(db_host):
        log(f"The host '{db_host}' is unreachable", "error")
        return(False)

    config = {
        'host': db_host,
        'user': db_user,
        'password': db_pass
    }

    if db_name != None:
        config["database"] = db_name

    conn = mysql.connector.connect(**config)

    print(conn.is_connected())

def getWpVersion():
    try:
        r = requests.get("https://api.wordpress.org/core/version-check/1.7/")
        if r.status_code == 200:
            data = r.json()
            lst = data['offers'][0]

            return({
                "version": lst["current"],
                "php_min": lst["php_version"],
                "mysql_min": lst["mysql_version"],
                "dlink": lst["download"]
            })
        else:
            log("Failed to fetch WordPress version", "error")
            return None
    except Exception as e:
        log(f"Error fetching WordPress version: {e}", "error")
        return None









def writeWpConfig(wp_directory, db_config, wp_config=None, backup=True):
    """
    Args:
        wp_directory (str): Répertoire WordPress
        db_config (dict): Configuration base de données
            {
                'db_name': 'nom_db',
                'db_user': 'utilisateur',
                'db_password': 'mot_de_passe',
                'db_host': 'localhost',
                'db_charset': 'utf8mb4',
                'db_collate': ''
            }
        wp_config (dict, optional): Configuration WordPress additionnelle
            {
                'table_prefix': 'wp_',
                'debug': False,
                'debug_log': False,
                'wp_memory_limit': '256M',
                'custom_constants': {'WP_CACHE': True}
            }
        backup (bool): Créer un backup avant modification
    """
    
    if wp_config is None:
        wp_config = {}
    
    config_file = os.path.join(wp_directory, 'wp-config.php')
    sample_file = os.path.join(wp_directory, 'wp-config-sample.php')
    
    result = {
        'success': False,
        'action': '',
        'message': '',
        'backup_file': None
    }
    
    try:
        if os.path.exists(config_file):
            result = _modify_existing_config(config_file, db_config, wp_config, backup)

        elif os.path.exists(sample_file):
            shutil.copyfile(sample_file, config_file)

            result = _modify_existing_config(config_file, db_config, wp_config, False)
            
        else:
            r = requests.get("https://raw.githubusercontent.com/WordPress/WordPress/refs/heads/master/wp-config-sample.php")
            if r.status_code != 200:
                print(f"      Failed to download the sample config file.\nUsing the static config")
                
                with open(config_file, "w") as f:
                    f.write(WP_CONFIG_SAMPLE)
            else:
                with open(sample_file, "wb") as f:
                    f.write(r.content)
                
                shutil.copyfile(sample_file, config_file)
            
            result = _modify_existing_config(config_file, db_config, wp_config, False)
        
        if result['success']:
            validation = _validate_config(config_file)
            if not validation['valid']:
                result['success'] = False
                result['message'] += f" Validation échouée: {validation['error']}"
        
        return result
        
    except Exception as e:
        return {
            'success': False,
            'action': 'error',
            'message': f'Erreur lors de la configuration: {str(e)}',
            'backup_file': None
        }

def _modify_existing_config(config_file, db_config, wp_config, backup):
    """Modifier un fichier wp-config.php existant"""
    
    result = {
        'success': True,
        'action': 'modified',
        'message': 'Configuration existante modifiée',
        'backup_file': None
    }
    
    if backup:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_file = f"{config_file}.backup_{timestamp}"
        shutil.copy2(config_file, backup_file)
        result['backup_file'] = backup_file
    
    with open(config_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    content = _update_db_constants(content, db_config)
    content = _update_wp_constants(content, wp_config)
    content = _replace_security_keys(content)
    
    with open(config_file, 'w', encoding='utf-8') as f:
        f.write(content)
    
    return result

def _update_db_constants(content, db_config):
    db_constants = {}
    for vr in ['DB_NAME','DB_USER','DB_PASSWORD','DB_HOST','DB_CHARSET','DB_COLLATE']:
        if vr in db_config.keys():
            db_constants[vr] = db_config[vr]
    
    #db_constants = {
    #    'DB_NAME': db_config['db_name'],
    #    'DB_USER': db_config['db_user'],
    #    'DB_PASSWORD': db_config['db_password'],
    #    'DB_HOST': db_config['db_host'],
    #    'DB_CHARSET': db_config['db_charset'],
    #    'DB_COLLATE': db_config['db_collate']
    #}
    
    for constant, value in db_constants.items():
        pattern = rf"define\s*\(\s*['\"]?{constant}['\"]?\s*,\s*['\"][^'\"]*['\"][^)]*\)\s*;"
        replacement = f"define('{constant}', '{value}');"
        
        if re.search(pattern, content, re.IGNORECASE):
            content = re.sub(pattern, replacement, content, flags=re.IGNORECASE)
        else:
            db_section_start = content.find("// ** Database settings")
            if db_section_start != -1:
                insertion_point = content.find("\n", db_section_start)
                content = content[:insertion_point] + f"\n{replacement}" + content[insertion_point:]
            else:
                php_tag = content.find("<?php")
                if php_tag != -1:
                    insertion_point = content.find("\n", php_tag)
                    content = content[:insertion_point] + f"\n{replacement}" + content[insertion_point:]
    
    return content

def _update_wp_constants(content, wp_config):
    if "table_prefix" in wp_config.keys():
        prefix_pattern = r'\$table_prefix\s*=\s*[\'"][^\'"]*[\'"];'
        prefix_replacement = f"$table_prefix = '{wp_config['table_prefix']}';"
        
        if re.search(prefix_pattern, content):
            content = re.sub(prefix_pattern, prefix_replacement, content)
        elif "/* That's all, stop editing!" in content:
            content = content.replace(
                "/* That's all, stop editing",
                f"{prefix_replacement}\n/* That's all, stop editing"
            )
        else:
            ptn = r"if\s*\(\s*!\s*defined\s*\(\s*['\"]?ABSPATH['\"]?\s*\)\s*\)\s*\{"
            m = re.search(ptn, content, re.IGNORECASE)
            print(m)
    
    wp_constants = {}
    for vr in ["WP_DEBUG","WP_DEBUG_LOG","WP_MEMORY_LIMIT"]:
        if vr in wp_config.keys():
            wp_constants[vr] = wp_config[vr]
    
    for constant, value in wp_constants.items():
        pattern = rf"define\s*\(\s*['\"]?{constant}['\"]?\s*,\s*[^)]+\)\s*;"
        replacement = f"define('{constant}', {value});"
        
        if re.search(pattern, content, re.IGNORECASE):
            content = re.sub(pattern, replacement, content, flags=re.IGNORECASE)
        elif "/* That's all, stop editing!" in content:
            content = content.replace(
                "/* That's all, stop editing!",
                f"define('{constant}', {value});\n\n/* That's all, stop editing!"
            )
        else:
            ptn = r"if\s*\(\s*!\s*defined\s*\(\s*['\"]?ABSPATH['\"]?\s*\)\s*\)\s*\{"
            m = re.search(ptn, content, re.IGNORECASE)
            pos = m.start()
            content = content[:pos] + "\n" + replacement + "\n\n" + content[pos:]
    
    return content

def _replace_security_keys(content):
    key_names = [
        'AUTH_KEY', 'SECURE_AUTH_KEY', 'LOGGED_IN_KEY', 'NONCE_KEY',
        'AUTH_SALT', 'SECURE_AUTH_SALT', 'LOGGED_IN_SALT', 'NONCE_SALT'
    ]

    new_keys = {}

    try:
        response = requests.get('https://api.wordpress.org/secret-key/1.1/salt/', timeout=10)
        if response.status_code == 200:
            r = response.text.strip()
            for l in r.split("\n"):
                kn = l.split("define('")[1].split("',")[0]
                kv = l.split("');")[0].split("'")[-1]
                new_keys[kn] = kv
        else:
            raise Exception("API indisponible")
            
    except:
        new_keys = _generate_security_keys(key_names)

    kta=[]
    lkp = None
    for key_name in key_names:
        key_pattern = rf"define\s*\(\s*['\"]?{key_name}['\"]?\s*,\s*['\"][^'\"]*['\"][^)]*\)\s*;"
        new_definition = f"define('{key_name}', '{new_keys[key_name]}');"

        mtc = re.search(key_pattern, content, re.IGNORECASE)
        if mtc:
            lkp = mtc.end()
            content = re.sub(key_pattern, new_definition, content, flags=re.IGNORECASE)
        else:
            kta.append(new_definition)
    
    if kta:
        if lkp != None and lkp != -1:
            lkp = content.find('\n', lkp)
            if lkp != -1:
                content = content[:lkp] + "\n" + ("\n".join(kta)) + content[lkp:]
                return(content)
        
        mtc = content.find("* Authentication unique keys and salts.")
        if mtc!=-1:
            mtc = content.find("*/", mtc)
            mtc = content.find('\n', mtc)
            if mtc != -1:
                content = content[:mtc] + "\n" + ("\n".join(kta)) + content[mtc:]
                return(content)

        if "/* That's all, stop editing!" in content:
            defs = '\n'.join(kta)
            content = content.replace(
                "/* That's all, stop editing!",
                f"{defs}\n\n/* That's all, stop editing!"
            )
        else:
            ptn = r"if\s*\(\s*!\s*defined\s*\(\s*['\"]?ABSPATH['\"]?\s*\)\s*\)\s*\{"
            m = re.search(ptn, content, re.IGNORECASE)
            pos = m.start()
            content = content[:pos] + "\n" + ("\n".join(kta)) + "\n\n" + content[pos:]

    return content

def _generate_security_keys(key_names):
    import secrets
    import string
    
    keys = {}
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*()-_=+[]{}|;:,.<>?"
    
    for key_name in key_names:
        key_value = ''.join(secrets.choice(alphabet) for _ in range(64))
        #keys_section += f"define('{key_name}', '{key_value}');\n"
        keys[key_name] = key_value
    
    return keys

def _validate_config(config_file):
    """Valider le fichier de configuration"""
    
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Vérifications de base
        required_constants = ['DB_NAME', 'DB_USER', 'DB_PASSWORD', 'DB_HOST']
        missing_constants = []
        
        for constant in required_constants:
            pattern = rf"define\s*\(\s*['\"]?{constant}['\"]?\s*,\s*['\"][^'\"]*['\"][^)]*\)\s*;"
            if not re.search(pattern, content, re.IGNORECASE):
                missing_constants.append(constant)
        
        if missing_constants:
            return {
                'valid': False,
                'error': f"Constantes manquantes: {', '.join(missing_constants)}"
            }
        
        # Vérifier la syntaxe PHP basique
        if not content.strip().startswith('<?php'):
            return {
                'valid': False,
                'error': "Le fichier ne commence pas par <?php"
            }
        
        return {'valid': True, 'error': None}
        
    except Exception as e:
        return {
            'valid': False,
            'error': f"Erreur de lecture: {str(e)}"
        }





if __name__=="__main__":
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--name", help="Nom du projet", required=False, default=None)
    parser.add_argument("--path", help="Chemin du site WP", required=False, default=None)
    parser.add_argument("--nodb", "-n", action="store_true", help="Ne crée pas de base de données", required=False, default=False)
    
    #parser.add_argument("input_path", help="File/Folder to convert")
    #parser.add_argument("--out_directory", "-o", help="Output directory", required=False, default="out")
    #parser.add_argument("--perimeter", help="Tenacy perimeter of the evaluation", required=False, default="perimeter_not_set")
    #parser.add_argument("--evaluator", help="Evaluator email address", required=False, default="seifeddine.allaya-ext@vc-partner.net")
    args = parser.parse_args()
    print(f"Args: {args}\n")

    print(f"🗃️ Database Setup")

    if args.name == None:
        dbExists = askBool("Est-ce la base de données existe déjà ?", default=False)
        if dbExists:
            while True:
                db_host = askText("Hôte", default="localhost")
                db_user = askText("Utilisateur")
                db_pass = askText("Mot de passe")

                r = checkDbConnection(db_host, db_user, db_pass)
                if r:
                    break
                else:
                    print(f"Connection to database failed")
        else:
            name = askText("Nom du projet")
    else:
        name = args.name
        dbExists=False
    
    if not dbExists:
        db_host = "localhost"

        if not args.nodb:
            dbl = listDb()

            name = formatName(name)
            dbn = f"wp_inst_{name}"

            if dbn in dbl:
                print(f"There's already a database named '{dbn}'")
                r = askBool("Do you want to replace it and the associated user ?", default=False)
                if r:
                    print(f"Dropping database '{dbn}' and its user\n")
                    runMysql(f"drop database {dbn};", True)
                    runMysql(f"drop user '{dbn}'@'localhost';", True)
                else:
                    print("Cancelling Installation\n")
                    exit()

            print(f"Creating objects for projet '{name}'")

            db_pass = createDb(dbn, f"wp_inst_{name}")

    print(f"\n\n🌐 Wordpress Setup\n")

    if args.path == None:
        ipath = askText(f"WP Installation path", "./")
    else:
        ipath = args.path
    


    while True:
        aipath = os.path.abspath(ipath)
        print(f"Installation path: {aipath}")

        if not os.path.exists(aipath):
            os.makedirs(aipath, exist_ok=True)
        else:
            ctn = os.listdir(aipath)
            wnb = len([1 for f in ["wp-admin","wp-content","wp-includes","wp-login.php","wp-load.php","wp-config.php","wp-settings.php"] if f in ctn])

            r = False
            for htd in ["public_html", "html"]:
                if htd in ctn:
                    r = askBool(f"Did you mean to use the '{htd}' directory ?", True)
                    if r:
                        ipath = os.path.join(ipath, "public_html")
                        break
            if r:
                continue

            if wnb>2:
                r = askChoice("The selected directory appears to already contain a WP installation.\nWhat do you wish to do ?", [
                    "Cancel installation",
                    "Keep installation and rewrite wp-config",
                    "Keep installation and update the database config",
                    "Rewrite entire installation",
                    "Keep wp-config, rewrite the rest"
                ], 0)
                
                if r==0:
                    print(f"Cancelling Installation\n")
                    exit()
                elif r==3:
                    print(f"Overwriting WP Installation")
                    break
                else:
                    print(f"This option is not yet available\n")
                    exit()
            
            elif len(ctn)!=0:
                if len(ctn)<=3:
                    ctns = f" ({', '.join(ctn)})"
                else:
                    ctns = f" ({', '.join(ctn[:3])} and {len(ctn)-3} more)"
                
                r = askBool(f"The directory is not empty{ctns}. Proceed anyway ?", False)
                if not r:
                    print(f"Cancelling Installation\n")
                    exit()
                else:
                    break
    
    
    print(f"\nInstalling WP in {aipath}")

    wpv = getWpVersion()
    if wpv is None:
        log("Failed to fetch WordPress version information", "error")
        exit()
    
    print(f"\nLast Wordpress version: {wpv['version']}")

    wpfn = f"/tmp/wp_{wpv['version']}.zip"

    if os.path.exists(wpfn):
        print("Wordpress archive already exists, skipping download")
    else:
        print("Downloading Wordpress 📥", end="", flush=True)
        r = requests.get(wpv['dlink'])

        if r.status_code != 200:
            log(f"      Failed to download Wordpress from {wpv['dlink']}", "error")
            exit()
        
        with open(wpfn, "wb") as f:
            f.write(r.content)
        print(f"    Wordpress downloaded ✅")
    
    print(f"\nExtracting Wordpress 📦")
    try:
        with zipfile.ZipFile(wpfn, 'r') as zipf:
            zipf.extractall("/tmp")
        
        if os.path.exists(aipath):
            shutil.rmtree(aipath)
        os.makedirs(aipath, exist_ok=True)
        
        shutil.copytree('/tmp/wordpress', aipath, dirs_exist_ok=True)
    except zipfile.BadZipFile:
        raise Exception(f"❌ Fichier ZIP corrompu")
    except Exception as e:
        raise Exception(f"❌ Erreur lors de l'extraction: {e}")

    print(f"\nWriting configuration 🎚️")

    db_conf = {
        'DB_NAME': dbn,
        "DB_USER": dbn,
        'DB_PASSWORD': db_pass,
        'DB_HOST': db_host
    }

    print(db_conf)

    writeWpConfig(aipath, db_conf, {

    }, False)

    print(f"\nInstallation is done 🪄\n")

        

        

