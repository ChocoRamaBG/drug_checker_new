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
    # Малка вметка: в Python директории и пътища се разделят с '/', а не със ',' 
    # (освен ако не е вътре във функция като os.path.join, там си е със запетайка)
except NameError:
    output_dir = os.getcwd()

# Екселът ТРЯБВА да е качен в същата папка в GitHub хранилището!
EXCEL_PATH = os.path.join(output_dir, "кодове с цени и ПБР за 2026г.xlsx")

# Вземаме URL-а от тайните на GitHub Actions
POWER_AUTOMATE_WEBHOOK_URL = os.environ.get("POWER_AUTOMATE_WEBHOOK_URL", "")
RECIPIENTS_LIST = "georgi.stoychev@sathealth.com, gogpoo@gmail.com, lyuben.vasilev@sathealth.com"

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
    Цвят без прозрачност, dark mode съвместим.
    Ако е отрицателно -> Тъмно червено.
    Ако е положително -> Тъмно зелено.
    """
    if percentage_diff < 0:
        return "background-color: #8b1a1a; color: #ffffff;"
    else:
        return "background-color: #1b5e20; color: #ffffff;"

def scrape_boomer_portal():
    today = datetime.datetime.now()
    
    print("Зареждаме твоите екселски редчовци...")
    try:
        df = pd.read_excel(EXCEL_PATH, sheet_name="Цени 180326")
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
        product_cards_html = "" 
        for row in scraped_data:
            badge_style = get_dynamic_color(row[6])
            
            # Outlook-proof table layout, no more dark mode misery, льольо!
            product_cards_html += f"""
            <table width="100%" border="0" cellpadding="0" cellspacing="0" style="border-bottom: 1px solid #e2e8f0; padding: 20px 0; margin-bottom: 5px;">
                <tr>
                    <td>
                        <div style="font-size: 13px; color: #64748b; padding-bottom: 8px; font-family: 'Segoe UI', Arial, sans-serif;">
                            <span style="font-weight:700; color:#2d4379; font-size:12px; margin-right: 5px; text-transform: uppercase;">код нзок:</span>{row[0]}
                        </div>
                        <div style="font-size: 13px; color: #64748b; padding-bottom: 14px; font-family: 'Segoe UI', Arial, sans-serif;">
                            <span style="font-weight:700; color:#2d4379; font-size:12px; margin-right: 5px; text-transform: uppercase;">код съвет:</span>{row[1]}
                        </div>
                        <div style="font-size: 16px; padding-bottom: 16px; line-height: 1.5; font-family: 'Segoe UI', Arial, sans-serif;">
                            <strong style="color: #1e293b;">{row[2]}</strong>
                        </div>
                        <table width="100%" border="0" cellpadding="0" cellspacing="0">
                            <tr>
                                <td style="padding-bottom: 8px;">
                                    <div style="font-size: 13px; color: #64748b; font-family: 'Segoe UI', Arial, sans-serif;">
                                        <span style="font-weight:700; color:#94a3b8; font-size:11px; text-transform: uppercase; margin-right: 5px;">ПРЕДХОДНА ЦЕНА ТЕ:</span>€{row[3]:.2f}
                                    </div>
                                </td>
                            </tr>
                            <tr>
                                <td style="padding-bottom: 14px;">
                                    <div style="font-size: 13px; color: #2d4379; font-family: 'Segoe UI', Arial, sans-serif;">
                                        <span style="font-weight:700; color:#2d4379; font-size:11px; text-transform: uppercase; margin-right: 5px;">НОВА ЦЕНА ТЕ:</span><strong>€{row[4]:.2f}</strong>
                                    </div>
                                </td>
                            </tr>
                            <tr>
                                <td>
                                    <span style="padding: 6px 12px; border-radius: 4px; font-weight: 700; font-size: 12px; display: inline-block; font-family: 'Segoe UI', Arial, sans-serif; {badge_style}">{row[5]}</span>
                                </td>
                            </tr>
                        </table>
                    </td>
                </tr>
            </table>
            """
        
        final_email_html = f"""
        <!DOCTYPE html>
        <html lang="bg">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
        </head>
        <body style="background-color: #f8fafc; margin: 0; padding: 0; font-family: 'Segoe UI', Arial, sans-serif;">
            <table width="100%" border="0" cellpadding="0" cellspacing="0" bgcolor="#f8fafc" style="padding: 20px 10px;">
                <tr>
                    <td align="center">
                        <table border="0" cellpadding="0" cellspacing="0" align="center" style="width: 100%; max-width: 600px; background-color: #ffffff; border-radius: 12px; border: 1px solid #e2e8f0; box-shadow: 0 2px 8px rgba(0,0,0,0.04);">
                            <tr>
                                <td>
                                    <table width="100%" border="0" cellpadding="0" cellspacing="0">
                                        <tr>
                                            <td style="background-color: #ffffff; padding: 25px 20px; border-radius: 12px 12px 0 0; border-bottom: 3px solid #2d4379; text-align: left;">
                                                <div style="color: #2d4379; font-size: 22px; font-weight: 800; margin-bottom: 5px; font-family: 'Segoe UI', Arial, sans-serif;">SAT Health Update</div>
                                                <div style="font-size: 13px; color: #64748b; font-family: 'Segoe UI', Arial, sans-serif;">Актуализация: {site_update_date}</div>
                                            </td>
                                        </tr>
                                    </table>

                                    <table width="100%" border="0" cellpadding="0" cellspacing="0">
                                        <tr>
                                            <td style="padding: 25px 20px;">
                                                <p style="margin: 0 0 15px 0; font-size: 15px; color: #334155; line-height: 1.6; font-family: 'Segoe UI', Arial, sans-serif;">
                                                    Следните продукти са с променени цени на ТЕ в Националния регистър:
                                                </p>
                                                {product_cards_html}
                                            </td>
                                        </tr>
                                    </table>

                                    <table width="100%" border="0" cellpadding="0" cellspacing="0">
                                        <tr>
                                            <td style="padding: 20px; font-size: 12px; color: #94a3b8; text-align: center; border-top: 1px solid #e2e8f0; background-color: #f8fafc; border-radius: 0 0 12px 12px; font-family: 'Segoe UI', Arial, sans-serif;">
                                                Строго конфиденциално. Генерирано на {current_date} от <strong style="color: #2d4379;">SAT Health Monitoring Systems</strong>.
                                            </td>
                                        </tr>
                                    </table>
                                </td>
                            </tr>
                        </table>
                    </td>
                </tr>
            </table>
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
