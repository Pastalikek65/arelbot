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
        print(f"Telegram Log Hatası: {e}")

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
        print("⚠️ RAILWAY_API_KEY veya RAILWAY_SERVICE_ID eksik. Railway Dashboard otomatik güncellenemedi.")
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
                print("✅ Railway Değişkenleri başarıyla güncellendi!")
                return True
            else:
                print(f"❌ Railway API Hatası: {response.status_code} - {response.text}")
                return False
    except Exception as e:
        print(f"Railway Update Hatası: {e}")
        return False

async def auto_fix_keys():
    """Checks all keys and replaces suspended OR exhausted keys with fresh ones."""
    print("\n🔍 [Auto-Fix] Anahtarlar taranıyor...")
    
    try:
        check_gcloud()
    except SystemExit:
        print("⚠️ GCloud CLI bulunamadı. Sadece tarama yapılacak, yenileme yapılamaz.")
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
            print(f"✅ Sağlam: {key[:5]}...")
        except Exception as e:
            error_msg = str(e).lower()
            if "suspended" in error_msg:
                print(f"🚫 SUSPENDED: {key[:5]}... - Değiştirilecek.")
                need_replacement.append(key)
            elif "quota" in error_msg or "429" in error_msg:
                print(f"⏳ KOTA DOLU: {key[:5]}... - AGRESİF YENİLEME: Değiştirilecek.")
                need_replacement.append(key)
            else:
                # Other errors (network, etc.) - keep them in pool for now
                print(f"⚠️ Bilinmeyen Hata (Kalsın): {key[:5]}... - {e}")
                active_keys.append(key)

    if not need_replacement:
        print("✨ Hiçbir bozuk/dolu key bulunmadı. Sistem temiz.")
        return

    print(f"\n🚨 {len(need_replacement)} adet key yenileniyor...")
    newly_generated_keys = []
    
    for _ in need_replacement:
        new_key = generate_new_key()
        if new_key:
            active_keys.append(new_key)
            newly_generated_keys.append(new_key)
            print(f"🎉 Yeni Key Üretildi: {new_key[:10]}...")
        else:
            print("❌ Yeni key üretilemedi!")

    # Update files and cloud
    if update_env_file(active_keys):
        print("💾 .env dosyası güncellendi!")
        
        await update_railway_variables(active_keys)
        
        log_msg = (
            f"🛠️ **AGRESİF ANAHTAR YENİLEME RAPORU**\n\n"
            f"🗑️ **Değiştirilen:** {len(need_replacement)} adet (Suspended/Quota)\n"
            f"✨ **Yeni Eklenen:** {len(newly_generated_keys)} adet\n"
            f"💪 **Toplam Sağlam Havuzu:** {len(active_keys)}\n\n"
            f"⚡ **Durum:** Railway ve .env otomatik güncellendi. Kesintisiz devam!"
        )
        await log_to_telegram(log_msg)
    else:
        print("❌ .env güncellenemedi!")

if __name__ == "__main__":
    print("🚀 Auto-Healing Sistemi Başlatıldı (Her 8 Saatte Bir Kontrol)")
    loop = asyncio.get_event_loop()
    
    while True:
        try:
            loop.run_until_complete(auto_fix_keys())
        except Exception as e:
            print(f"Main Loop Hatası: {e}")
            
        print("💤 8 Saat uyku moduna geçiliyor...")
        time.sleep(8 * 3600)
