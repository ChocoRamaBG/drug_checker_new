import os
import time
import re
import datetime
import json
import requests 
import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select    
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options

# Зареждаме директорията както трябва, не като гащник
try: 
    output_dir = os.path.dirname(os.path.abspath(__file__)) 
except NameError: 
    output_dir = os.getcwd()

# Екселът ТРЯБВА да е качен в същата папка в GitHub хранилището!
EXCEL_PATH = os.path.join(output_dir, "кодове с цени и ПБР за 2026г_.xlsx")

# Вземаме URL-а от тайните на GitHub Actions
POWER_AUTOMATE_WEBHOOK_URL = os.environ.get("POWER_AUTOMATE_WEBHOOK_URL", "")
RECIPIENTS_LIST = "georgi.stoychev@sathealth.com, gogpoo@gmail.com"

MEMORY_FILE_PATH = os.path.join(output_dir, "prices_memory.json")

# Оставяме True, защото GitHub Actions ще се грижи за графика през cron
TEST_MODE_SEND_NOW = True 

def load_memory():
    if os.path.exists(MEMORY_FILE_PATH):
        try:
            with open(MEMORY_FILE_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            print("Мамка му човече, JSON-ът ти е счупен.")
            return {}
    return {}

def save_memory(memory_data):
    with open(MEMORY_FILE_PATH, 'w', encoding='utf-8') as f:
        json.dump(memory_data, f, indent=4)

def send_to_power_automate(html_content):
    if not POWER_AUTOMATE_WEBHOOK_URL:
        print("Гащник, не си сложил URL-а в GitHub Secrets!")
        return
        
    print("Пращаме твоите мейлчовци през Power Automate...")
    payload = {
        "html_body": html_content,
        "recipients": RECIPIENTS_LIST,
        "subject": f"SAT Health Update - {datetime.datetime.now().strftime('%d.%m.%Y')}"
    }
    
    try:
        response = requests.post(POWER_AUTOMATE_WEBHOOK_URL, json=payload)
        if response.status_code in [200, 202]:
            print("Мейлът отлетя успешно! ¡Muy bien!")
        else:
            print(f"What the hell! Power automate върна грешка: {response.status_code}")
    except Exception as e:
        print(f"Тотален паприкаш при пращането: {e}")

def get_dynamic_color(percentage_diff):
    if percentage_diff < 0:
        return "background-color: #ef4444; color: #ffffff;"
    else:
        return "background-color: #22c55e; color: #ffffff;"

def scrape_boomer_portal():
    today = datetime.datetime.now()
    
    print("Зареждаме твоите екселски редчовци...")
    try:
        df = pd.read_excel(EXCEL_PATH, sheet_name="Цени")
    except Exception as e:
        print(f"What the hell, it's a паприкаш! Excel failed: {e}")
        return

    memory_prices = load_memory()
    
    # Headless режим за GitHub Actions, иначе гърми
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    
    driver = webdriver.Chrome(options=chrome_options)
    driver.get("https://portal.ncpr.bg/registers/pages/register/list-medicament.xhtml")

    try:
        title_div = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.title"))
        )
        b_element = title_div.find_element(By.TAG_NAME, "b")
        site_update_date = b_element.text
    except Exception:
        site_update_date = today.strftime("%d.%m.%Y")

    scraped_data = []
    memory_was_updated = False

    for index, row in df.iterrows():
        kod_nzok = str(row['Код НЗОК']) if not pd.isna(row['Код НЗОК']) else "N/A"
        kod_savet = row['Код на Съвета']
        excel_original_cena = row['Цена търговец на едро с ДДС в евро']

        if pd.isna(kod_savet): continue
            
        search_val = str(int(kod_savet))
        base_price = memory_prices.get(search_val, float(excel_original_cena))
        base_price = round(float(base_price), 2)

        try:
            select_element = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.ID, "medicamentSearchForm:register:register"))
            )
            Select(select_element).select_by_value("4")

            input_field = driver.find_element(By.ID, "medicamentSearchForm:medicamentIdentifier")
            input_field.clear()
            input_field.send_keys(search_val)

            search_btn = driver.find_element(By.ID, "medicamentSearchForm:fastSearchBtn")
            search_btn.click() 

            time.sleep(10)

            table_rows = driver.find_elements(By.XPATH, "//*[@id='medicamentSearchForm:resultRegisterFourTable:tb']/tr")
            
            if not table_rows or "rf-dt-empty" in table_rows[0].get_attribute("class"):
                continue
            
            tds = table_rows[0].find_elements(By.TAG_NAME, "td")
            
            if len(tds) > 11:
                drug_name = tds[1].text.replace('\n', ' ')
                site_price_str = tds[11].text
                clean_str = re.sub(r'[^\d,]', '', site_price_str).replace(',', '.')
                
                if clean_str:
                    site_price = round(float(clean_str), 2)
                    
                    if abs(site_price - base_price) > 0.02:
                        diff_pct = ((site_price - base_price) / base_price) * 100 if base_price > 0 else 0.0
                        pct_str = f"{diff_pct:+.2f}%"
                        
                        scraped_data.append([kod_nzok, search_val, drug_name, base_price, site_price, pct_str, diff_pct])
                        memory_prices[search_val] = site_price
                        memory_was_updated = True
        except Exception as e:
            print(f"Грешка за {search_val}: {e}")

    driver.quit()
    
    if memory_was_updated:
        save_memory(memory_prices)
    
    current_date = today.strftime("%d.%m.%Y")
    
    if len(scraped_data) > 0:
        # Тук си остава твоят HTML шит (съкращавам го, за да не спамя)
        product_cards_html = "".join([f"<p>{row[2]} - {row[5]}</p>" for row in scraped_data]) # Сложи си оригиналния HTML тук
        
        final_email_html = f"<html><body><h1>Промени!</h1>{product_cards_html}</body></html>" # Сложи си оригиналния HTML тук
        
        email_file_path = os.path.join(output_dir, "SAT_Health_Report.html")
        with open(email_file_path, "w", encoding="utf-8") as f:
            f.write(final_email_html)
            
        print(f"Репортчовците са готови в: {email_file_path}")
        send_to_power_automate(final_email_html)
    else:
        print("Няма промени в цените. Скипваме репортчовците.")

if __name__ == "__main__":
    scrape_boomer_portal()
