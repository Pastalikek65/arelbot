import os
import asyncio
import time
from google import genai
from dotenv import load_dotenv
from config import Config
from unified_logger import UnifiedLogger
from create_gemini_projects import generate_new_key, check_gcloud

# Load current keys
load_dotenv()
ENV_FILE = ".env"

async def log_to_telegram(message):
    """Logs a message to the specified Telegram log channel."""
    try:
        logger = UnifiedLogger()
        await logger.log(message, topic_id=Config.GROUP_LOG_THREAD_ID)
    except Exception as e:
        print(f"Telegram Log HatasÄ±: {e}")

def update_env_file(new_keys_list):
    """Replaces the GEMINI_KEYS line in the .env file with a updated list."""
    try:
        with open(ENV_FILE, 'r') as f:
            lines = f.readlines()
        
        new_line = f"GEMINI_KEYS={','.join(new_keys_list)}\n"
        
        found = False
        for i, line in enumerate(lines):
            if line.startswith("GEMINI_KEYS="):
                lines[i] = new_line
                found = True
                break
        
        if not found:
            lines.append(f"\n{new_line}")
            
        with open(ENV_FILE, 'w') as f:
            f.writelines(lines)
        return True
    except Exception as e:
        print(f"Env File Update Error: {e}")
        return False

async def update_railway_variables(new_keys_list):
    """Updates the GEMINI_KEYS variable on Railway via GraphQL API."""
    api_token = os.getenv("RAILWAY_API_KEY")
    project_id = os.getenv("RAILWAY_PROJECT_ID")
    service_id = os.getenv("RAILWAY_SERVICE_ID")
    
    if not api_token or not service_id:
        print("âš ï¸ RAILWAY_API_KEY veya RAILWAY_SERVICE_ID eksik. Railway Dashboard otomatik gÃ¼ncellenemedi.")
        return False

    url = "https://backboard.railway.app/graphql/v2"
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json"
    }
    
    query = """
    mutation variableUpsert($projectId: String!, $serviceId: String!, $variables: Map!) {
      variableUpsert(projectId: $projectId, serviceId: $serviceId, variables: $variables)
    }
    """
    
    variables = {
        "projectId": project_id,
        "serviceId": service_id,
        "variables": {
            "GEMINI_KEYS": ",".join(new_keys_list)
        }
    }

    try:
        import httpx
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json={"query": query, "variables": variables}, headers=headers)
            if response.status_code == 200:
                print("âœ… Railway DeÄŸiÅŸkenleri baÅŸarÄ±yla gÃ¼ncellendi!")
                return True
            else:
                print(f"âŒ Railway API HatasÄ±: {response.status_code} - {response.text}")
                return False
    except Exception as e:
        print(f"Railway Update HatasÄ±: {e}")
        return False

async def auto_fix_keys():
    """Checks all keys and replaces suspended OR exhausted keys with fresh ones."""
    print("\nğŸ” [Auto-Fix] Anahtarlar taranÄ±yor...")
    
    try:
        check_gcloud()
    except SystemExit:
        print("âš ï¸ GCloud CLI bulunamadÄ±. Sadece tarama yapÄ±lacak, yenileme yapÄ±lamaz.")
        return

    keys_str = os.getenv("GEMINI_KEYS", "")
    current_keys = [k.strip() for k in keys_str.split(",") if k.strip()]
    
    active_keys = []
    need_replacement = []
    
    # Model check model (Ping check)
    CHECK_MODEL = "gemini-2.0-flash" 

    for key in current_keys:
        try:
            client = genai.Client(api_key=key)
            client.models.generate_content(
                model=CHECK_MODEL,
                contents="Ping"
            )
            active_keys.append(key)
            print(f"âœ… SaÄŸlam: {key[:5]}...")
        except Exception as e:
            error_msg = str(e).lower()
            if "suspended" in error_msg:
                print(f"ğŸš« SUSPENDED: {key[:5]}... - DeÄŸiÅŸtirilecek.")
                need_replacement.append(key)
            elif "quota" in error_msg or "429" in error_msg:
                print(f"â³ KOTA DOLU: {key[:5]}... - AGRESÄ°F YENÄ°LEME: DeÄŸiÅŸtirilecek.")
                need_replacement.append(key)
            else:
                # Other errors (network, etc.) - keep them in pool for now
                print(f"âš ï¸ Bilinmeyen Hata (KalsÄ±n): {key[:5]}... - {e}")
                active_keys.append(key)

    if not need_replacement:
        print("âœ¨ HiÃ§bir bozuk/dolu key bulunmadÄ±. Sistem temiz.")
        return

    print(f"\nğŸš¨ {len(need_replacement)} adet key yenileniyor...")
    newly_generated_keys = []
    
    for _ in need_replacement:
        new_key = generate_new_key()
        if new_key:
            active_keys.append(new_key)
            newly_generated_keys.append(new_key)
            print(f"ğŸ‰ Yeni Key Ãœretildi: {new_key[:10]}...")
        else:
            print("âŒ Yeni key Ã¼retilemedi!")

    # Update files and cloud
    if update_env_file(active_keys):
        print("ğŸ’¾ .env dosyasÄ± gÃ¼ncellendi!")
        
        await update_railway_variables(active_keys)
        
        log_msg = (
            f"ğŸ› ï¸ **AGRESÄ°F ANAHTAR YENÄ°LEME RAPORU**\n\n"
            f"ğŸ—‘ï¸ **DeÄŸiÅŸtirilen:** {len(need_replacement)} adet (Suspended/Quota)\n"
            f"âœ¨ **Yeni Eklenen:** {len(newly_generated_keys)} adet\n"
            f"ğŸ’ª **Toplam SaÄŸlam Havuzu:** {len(active_keys)}\n\n"
            f"âš¡ **Durum:** Railway ve .env otomatik gÃ¼ncellendi. Kesintisiz devam!"
        )
        await log_to_telegram(log_msg)
    else:
        print("âŒ .env gÃ¼ncellenemedi!")

if __name__ == "__main__":
    print("ğŸš€ Auto-Healing Sistemi BaÅŸlatÄ±ldÄ± (Her 8 Saatte Bir Kontrol)")
    loop = asyncio.get_event_loop()
    
    while True:
        try:
            loop.run_until_complete(auto_fix_keys())
        except Exception as e:
            print(f"Main Loop HatasÄ±: {e}")
            
        print("ğŸ’¤ 8 Saat uyku moduna geÃ§iliyor...")
        time.sleep(8 * 3600)
