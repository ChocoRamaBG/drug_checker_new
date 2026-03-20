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
    """
    Цвят без прозрачност.
    Ако е отрицателно -> Червено.
    Ако е положително -> Зелено.
    """
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

            time.sleep(10) # Оставям го 10 сек за headless, че е по-бавно

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
    product_rows_html = ""
    for row in scraped_data:
        badge_style = get_dynamic_color(row[6])
        
        product_cards_html += f"""
        <div class="product-card" style="border-bottom: 1px solid #f1f5f9; padding: 15px 0;">
            <table width="100%" border="0" cellpadding="0" cellspacing="0">
                <tr>
                    <td class="pc-col-desktop" style="width: 15%; font-size: 13px; color: #64748b; padding-bottom: 5px;">
                        <span class="m-label" style="display:none; font-weight:700; color:#94a3b8; font-size:10px;">КОД НЗОК: </span>{row[0]}
                    </td>
                    <td class="pc-col-desktop" style="width: 15%; font-size: 13px; color: #64748b; padding-bottom: 5px;">
                        <span class="m-label" style="display:none; font-weight:700; color:#94a3b8; font-size:10px;">КОД СЪВЕТ: </span>{row[1]}
                    </td>
                    <td class="pc-col-desktop" style="width: 35%; font-size: 14px; padding-bottom: 5px;">
                        <strong style="color: #0f172a;">{row[2]}</strong>
                    </td>
                    <td class="pc-col-desktop" style="width: 12%; font-size: 13px; padding-bottom: 5px;">
                        <span class="m-label" style="display:none; font-weight:700; color:#94a3b8; font-size:10px;">ПРЕДХОДНА ЦЕНА ТЕ: </span>€{row[3]:.2f}
                    </td>
                    <td class="pc-col-desktop" style="width: 12%; font-size: 13px; padding-bottom: 5px;">
                        <span class="m-label" style="display:none; font-weight:700; color:#94a3b8; font-size:10px;">НОВА ЦЕНА ТЕ: </span>€{row[4]:.2f}
                    </td>
                    <td class="pc-col-desktop" style="width: 11%; font-size: 13px; text-align: right; padding-bottom: 5px;">
                        <span style="padding: 4px 10px; border-radius: 4px; font-weight: 800; font-size: 11px; display: inline-block; {badge_style}">{row[5]}</span>
                    </td>
                </tr>
            </table>
        </div>
        """
    
    final_email_html = f"""
    <!DOCTYPE html>
    <html lang="bg">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body {{ background-color: #f1f5f9; margin: 0; padding: 0; font-family: 'Segoe UI', Arial, sans-serif; }}
            @media only screen and (max-width: 600px) {{
                .email-container {{ width: 100% !important; }}
                .header, .content-card {{ padding: 20px !important; }}
                .subtitle-table {{ display: block !important; width: 100% !important; }}
                .subtitle-cell {{ display: block !important; width: 100% !important; text-align: left !important; }}
                .subtitle-cell-right {{ padding-top: 10px !important; }}
                
                .desktop-thead {{ display: none !important; }}
                .product-card {{ padding: 20px 0 !important; }}
                .pc-col-desktop {{ display: block !important; width: 100% !important; text-align: left !important; padding-bottom: 8px !important; }}
                .m-label {{ display: inline-block !important; margin-right: 5px !important; }}
                .pc-col-desktop:last-child {{ text-align: left !important; padding-top: 5px !important; }}
            }}
        </style>
    </head>
    <body style="background-color: #f1f5f9; margin: 0; padding: 0; font-family: 'Segoe UI', Arial, sans-serif;">
        <div style="width: 100%; background-color: #f1f5f9; padding: 20px 0;">
            <table width="100%" border="0" cellpadding="0" cellspacing="0" style="max-width: 850px; margin: 0 auto;">
                <tr>
                    <td>
                        <!-- Header -->
                        <div style="background-color: #0f172a; padding: 30px 40px; border-radius: 12px 12px 0 0; border-bottom: 4px solid #38bdf8; color: #ffffff;">
                            <div style="color: #38bdf8; font-size: 26px; font-weight: 800; letter-spacing: 1px; margin-bottom: 5px;">SAT HEALTH</div>
                            <h2 style="margin: 0; font-size: 22px; font-weight: 600;">Отчет: Промяна в цените на ПЛС</h2>
                            
                            <div style="margin-top: 15px; border-top: 1px solid rgba(255,255,255,0.1); padding-top: 15px;">
                                <table class="subtitle-table" width="100%" border="0" cellpadding="0" cellspacing="0">
                                    <tr>
                                        <td class="subtitle-cell" style="font-size: 13px; color: #94a3b8; font-weight: 300; text-align: left;">Автоматизирана проверка | Приложение № 4</td>
                                        <td class="subtitle-cell subtitle-cell-right" style="font-size: 12px; color: #cbd5e1; text-align: right;">Актуализация: <strong style="color:#38bdf8;">{site_update_date}</strong></td>
                                    </tr>
                                </table>
                            </div>
                        </div>

                        <!-- Content Card -->
                        <div class="content-card" style="background-color: #ffffff; padding: 40px; border-radius: 0 0 12px 12px; border: 1px solid #e2e8f0; border-top: none;">
                            <p style="margin: 0 0 18px 0; font-size: 15px; color: #334155; line-height: 1.6;">Здравейте,</p>
                            <p style="margin: 0 0 25px 0; font-size: 15px; color: #334155; line-height: 1.6;">Автоматизираната проверка приключи успешно. Открити са следните продукти с променени цени на ТЕ в Националния регистър:</p>

                            <!-- Header row only for desktop -->
                            <table class="desktop-thead" width="100%" border="0" cellpadding="0" cellspacing="0" style="background-color: #f8fafc; border: 1px solid #f1f5f9; border-bottom: 2px solid #f1f5f9;">
                                <tr>
                                    <th style="width: 15%; padding: 12px 10px; text-align: left; font-size: 10px; color: #64748b; text-transform: uppercase; letter-spacing: 1px;">Код НЗОК</th>
                                    <th style="width: 15%; padding: 12px 10px; text-align: left; font-size: 10px; color: #64748b; text-transform: uppercase; letter-spacing: 1px;">Код Съвет</th>
                                    <th style="width: 35%; padding: 12px 10px; text-align: left; font-size: 10px; color: #64748b; text-transform: uppercase; letter-spacing: 1px;">Продукт</th>
                                    <th style="width: 12%; padding: 12px 10px; text-align: left; font-size: 10px; color: #64748b; text-transform: uppercase; letter-spacing: 1px;">Стара</th>
                                    <th style="width: 12%; padding: 12px 10px; text-align: left; font-size: 10px; color: #64748b; text-transform: uppercase; letter-spacing: 1px;">Нова</th>
                                    <th style="width: 11%; padding: 12px 10px; text-align: right; font-size: 10px; color: #64748b; text-transform: uppercase; letter-spacing: 1px;">Ръст</th>
                                </tr>
                            </table>

                            <!-- List of products -->
                            {product_cards_html}

                            
                        </div>

                        <div style="padding: 25px 30px; font-size: 11px; color: #94a3b8; text-align: center;">
                            Строго конфиденциално. Генерирано на {current_date} от SAT Health Monitoring Systems.
                        </div>
                    </td>
                </tr>
            </table>
        </div>
    </body>
    </html>
    """
    
    
    email_file_path = os.path.join(output_dir, "SAT_Health_Report.html")
    with open(email_file_path, "w", encoding="utf-8") as f:
        f.write(final_email_html)
        
    print(f"Репортчовците са готови в: {email_file_path}")
    send_to_power_automate(final_email_html)
else:
    print("Няма промени в цените. Скипваме репортчовците.")

if __name__ == "__main__":
    scrape_boomer_portal()
