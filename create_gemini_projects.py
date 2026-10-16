import subprocess
import json
import random
import string
import time
import sys
import shutil
import os

# List of accounts to rotate through if one hits checking limits
GOOGLE_ACCOUNTS = os.getenv("GOOGLE_ACCOUNTS", "").split(",")

def run_command(command):
    """Runs a shell command and returns the output and error."""
    try:
        result = subprocess.run(
            command, 
            capture_output=True, 
            text=True, 
            shell=True,
            encoding='utf-8'
        )
        return result.stdout.strip(), result.stderr.strip(), result.returncode
    except Exception as e:
        return "", str(e), -1

def check_gcloud():
    """Checks if gcloud CLI is installed."""
    if not shutil.which("gcloud"):
        print("âŒ HATA: 'gcloud' CLI yÃ¼klÃ¼ deÄŸil!")
        sys.exit(1)

def get_current_account():
    stdout, _, _ = run_command("gcloud config get-value account")
    return stdout.strip()

def switch_account(email):
    """Switches the active gcloud account."""
    if not email: return False
    print(f"ğŸ”„ Hesap deÄŸiÅŸtiriliyor: {email}...")
    stdout, stderr, code = run_command(f"gcloud config set account {email}")
    if code == 0:
        print(f"âœ… Hesap aktif: {email}")
        return True
    else:
        print(f"âŒ Hesap deÄŸiÅŸtirme hatasÄ±: {stderr}")
        return False

def list_projects():
    """Lists all Google Cloud projects for the current account."""
    stdout, stderr, code = run_command("gcloud projects list --format=json")
    if code == 0:
        try:
            return json.loads(stdout)
        except:
            return []
    return []

def create_project(base_name="antigravity-bot"):
    """Creates a new Google Cloud Project."""
    suffix = ''.join(random.choices(string.digits, k=6))
    project_id = f"{base_name}-{suffix}"
    
    print(f"ğŸ”¨ Proje oluÅŸturuluyor: {project_id}...")
    cmd = f"gcloud projects create {project_id} --name=\"{project_id}\" --format=json"
    stdout, stderr, code = run_command(cmd)
    
    if code != 0:
        err_lower = stderr.lower()
        if "quota" in err_lower or "limit" in err_lower:
            print(f"âš ï¸ PROJE LÄ°MÄ°TÄ° DOLDU: {stderr[:100]}...")
            return "LIMIT_REACHED"
        print(f"âŒ Proje oluÅŸturma hatasÄ±: {stderr}")
        return None
    
    print(f"âœ… Proje oluÅŸturuldu: {project_id}")
    return project_id

def enable_api(project_id):
    """Enables the Generative Language API (Gemini) and API Keys API."""
    print(f"ğŸ”Œ API'ler aktif ediliyor: {project_id}...")
    
    apis = ["generativelanguage.googleapis.com", "apikeys.googleapis.com"]
    for api in apis:
        for _ in range(3):
            cmd = f"gcloud services enable {api} --project {project_id}"
            stdout, stderr, code = run_command(cmd)
            if code == 0: break
            time.sleep(2)
        if code != 0:
            print(f"âŒ {api} aÃ§ma hatasÄ±: {stderr[:100]}")
    
    print("âœ… API'ler hazÄ±r.")
    return True

def get_api_key_command_group():
    """Determines which command group to use (beta, alpha, or standard)."""
    # Check standard
    _, stderr, code = run_command("gcloud services api-keys --help")
    if code == 0: return "services api-keys"
    
    # Check beta
    _, stderr, code = run_command("gcloud beta services api-keys --help")
    if code == 0: return "beta services api-keys"
    
    # Check alpha
    _, stderr, code = run_command("gcloud alpha services api-keys --help")
    if code == 0: return "alpha services api-keys"
    
    return None

def create_api_key(project_id):
    """Creates an API Key for the project."""
    group = get_api_key_command_group()
    if not group:
        print("âŒ HATA: 'api-keys' komut grubu bulunamadÄ±!")
        return None

    print(f"ğŸ”‘ API Key oluÅŸturuluyor ({group}): {project_id}...")
    
    # Try to create and get the key directly from the output
    cmd = f"gcloud {group} create --display-name=\"Gemini Key\" --project {project_id} --format=json"
    stdout, stderr, code = run_command(cmd)
    
    if code == 0 and stdout:
        try:
            # The create command output in JSON format often contains the keyString directly
            res = json.loads(stdout)
            # Depending on gcloud version, it might be in 'response.keyString' or just 'keyString'
            key = None
            if isinstance(res, dict):
                key = res.get('keyString') or res.get('response', {}).get('keyString')
            
            if key:
                print(f"ğŸ‰ KEY DOÄRUDAN ALINDI: {key}")
                return key
        except:
            pass

    # If direct extraction failed, but command succeeded, wait and try to list
    if code == 0:
        print("â³ Anahtar oluÅŸturuldu, listeden Ã§ekilmeye Ã§alÄ±ÅŸÄ±lÄ±yor (15 sn)...")
        time.sleep(15)
        
        cmd_list = f"gcloud {group} list --project {project_id} --format=json"
        l_stdout, l_stderr, l_code = run_command(cmd_list)
        
        if l_code == 0 and l_stdout:
            try:
                keys = json.loads(l_stdout)
                if keys and isinstance(keys, list):
                    key_string = keys[0].get('keyString')
                    if key_string:
                        print(f"ğŸ‰ KEY LÄ°STEDEN ALINDI: {key_string}")
                        return key_string
            except Exception as e:
                print(f"âš ï¸ Liste ayrÄ±ÅŸtÄ±rma hatasÄ±: {e}")
    else:
        # Check for specific known errors
        if "PERMISSION_DENIED" in stderr:
            print("âŒ YETKÄ° HATASI: Bu hesapta API Key oluÅŸturma yetkiniz yok veya proje henÃ¼z hazÄ±r deÄŸil.")
        elif "BILLING_ENABLED" in stderr:
            print("âŒ FATURALANDIRMA HATASI: Bu API iÃ§in faturalandÄ±rma hesabÄ± baÄŸlÄ± olmalÄ± (Ancak Gemini genelde Ã¼cretsizdir).")
        else:
            print(f"âŒ Key oluÅŸturma baÅŸarÄ±sÄ±z: {stderr[:300]}")

    print("âŒ Anahtar Ã§ekilemedi.")
    return None

def ensure_keys_for_existing():
    """Scans existing 'antigravity-bot-*' projects and ensures they have Gemini keys."""
    print("\nğŸ” Mevcut projeler taranÄ±yor...")
    projects = list_projects()
    bot_projects = [p for p in projects if p.get('projectId', '').startswith('antigravity-bot-')]
    
    print(f"ğŸ“‚ Toplam {len(bot_projects)} adet bot projesi bulundu.")
    
    new_keys_count = 0
    group = get_api_key_command_group()
    
    for p in bot_projects:
        pid = p['projectId']
        # Check if key exists
        if group:
            stdout, _, code = run_command(f"gcloud {group} list --project {pid} --format=json")
            try:
                keys = json.loads(stdout) if code == 0 and stdout else []
                if keys:
                    print(f"âœ… {pid} projesinde zaten anahtar var.")
                    continue
            except:
                pass
        
        print(f"âš™ï¸ {pid} iÃ§in anahtar eksik. OluÅŸturuluyor...")
        enable_api(pid)
        key = create_api_key(pid)
        if key:
            with open("new_generated_keys.txt", "a") as f:
                f.write(f"{pid}={key}\n")
            new_keys_count += 1
            
    print(f"âœ… Mevcut projeler tamamlandÄ±. {new_keys_count} yeni anahtar eklendi.")

def generate_new_key():
    """Creates a brand new project and key."""
    # Logic remains similar to previous version but with better group detection
    project_id = create_project()
    
    if project_id == "LIMIT_REACHED":
        return "LIMIT_REACHED"

    if project_id:
        time.sleep(5)
        if enable_api(project_id):
            key = create_api_key(project_id)
            if key:
                with open("new_generated_keys.txt", "a") as f:
                    f.write(f"{project_id}={key}\n")
                return "SUCCESS"
    return "FAILED"

def main():
    print("\n" + "="*50)
    print("ğŸš€ GOOGLE CLOUD SUPER KEY GENERATOR")
    print("="*50)
    check_gcloud()
    
    print("\n1. Mevcut projeleri tara ve eksik anahtarlarÄ± oluÅŸtur.")
    print("2. SÄ±fÄ±rdan yeni projeler oluÅŸturmaya baÅŸla (SÄ±nÄ±rsÄ±z Mod).")
    print("3. Her ikisini de yap.")
    choice = input("\nSeÃ§imin (1/2/3): ")

    if choice in ["1", "3"]:
        ensure_keys_for_existing()
    
    if choice in ["2", "3"]:
        print("\nâš¡ SÄ±nÄ±rsÄ±z proje Ã¼retimi baÅŸlÄ±yor...")
        count = 0
        while True:
            count += 1
            print(f"\n--- ğŸ”¨ Ä°ÅŸlem No: {count} ---")
            status = generate_new_key()
            if status == "LIMIT_REACHED":
                print("âŒ Hesap limiti doldu.")
                break
            time.sleep(10)

if __name__ == "__main__":
    main()
