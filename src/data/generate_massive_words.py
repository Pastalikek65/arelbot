import json
import os
import requests
import random

def generate_massive_words():
    # 1. Load existing categorized words
    current_dir = os.path.dirname(os.path.abspath(__file__))
    json_path = os.path.join(current_dir, "words.json")
    
    existing_words = []
    if os.path.exists(json_path):
        with open(json_path, "r", encoding="utf-8") as f:
            existing_words = json.load(f)
            
    existing_word_texts = {w['word'] for w in existing_words}
    print(f"Loaded {len(existing_words)} existing words.")

    # 2. Fetch large Turkish frequency list
    url = "https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/tr/tr_50k.txt"
    try:
        response = requests.get(url)
        response.raise_for_status()
        lines = response.text.splitlines()
    except Exception as e:
        print(f"Failed to download word list: {e}")
        return

    new_words = []
    # Filter and process
    # We ignore very common short words and functional words
    ignored_prefixes = ("bi", "bu", "ne", "ve", "de", "da", "mi", "mı", "mu", "mü")
    
    for line in lines[:8000]: # Take top 8k to filter down to ~3k-4k high quality
        parts = line.split()
        if len(parts) != 2: continue
        word = parts[0]
        
        # Filtering criteria:
        if len(word) < 4: continue
        if word.lower().startswith(ignored_prefixes): continue
        if not word.isalpha(): continue
        if word in existing_word_texts: continue
        
        # Add to new words with generic info
        new_words.append({
            "word": word,
            "category": "Genel",
            "description": "Bu yaygın bir Türkçe kelimedir. Tahmin etmeye çalışın!",
            "hints": [
                f"{word[0]} harfi ile başlar.",
                f"Kelime {len(word)} harflidir.",
                f"Son harfi {word[-1]}."
            ]
        })
        existing_word_texts.add(word)
        
        if len(new_words) >= 3000: # Limit to ~3000 new words
            break

    # 3. Merge and Save
    all_words = existing_words + new_words
    print(f"Total words after merge: {len(all_words)}")
    
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(all_words, f, ensure_ascii=False, indent=2)
        
    print(f"Saved {len(all_words)} words to {json_path}")

if __name__ == "__main__":
    generate_massive_words()
