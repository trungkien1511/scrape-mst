import re
import time
import requests
from bs4 import BeautifulSoup
import gspread
from oauth2client.service_account import ServiceAccountCredentials

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
}
BASE_URL = "https://masothue.com"

# ----------------------------
# 1. Cào dữ liệu
# ----------------------------
def parse_list_page(html: str):
    soup = BeautifulSoup(html, "lxml")
    results = []
    for block in soup.select("div.tax-listing div[data-prefetch]"):
        h3a = block.select_one("h3 > a")
        company_name = h3a.get_text(strip=True) if h3a else ""
        tax_code = ""
        info_div = block.find("div")
        if info_div:
            for a in info_div.select("a[title]"):
                m = re.search(r"\b(\d{8,15})\b", a.get_text(strip=True))
                if m:
                    tax_code = m.group(1)
                    break
        path = h3a["href"] if h3a and h3a.has_attr("href") else None
        if company_name and tax_code and path:
            results.append({"name": company_name, "tax_code": tax_code, "link": path})
    return results

def fetch_company_phone(path: str, delay: float = 1.5) -> str:
    url = BASE_URL + path
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")
    phone_td = soup.select_one("td[itemprop='telephone'] span.copy")
    phone = phone_td.get_text(strip=True) if phone_td else ""
    time.sleep(delay)
    return phone

# ----------------------------
# 2. Lưu Google Sheet
# ----------------------------
def save_to_google_sheet(data, sheet_url, sheet_name="Sheet1", creds_file="credentials.json"):
    scope = ["https://spreadsheets.google.com/feeds", 
             "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(creds_file, scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_url(sheet_url).worksheet(sheet_name)
    sheet.clear()
    sheet.append_row(["Tên doanh nghiệp", "Mã số thuế", "Số điện thoại"])
    for row in data:
        sheet.append_row([row["name"], row["tax_code"], row.get("phone", "")])
    print("✔ Đã lưu dữ liệu vào Google Sheet!")

# ----------------------------
# 3. Chạy
# ----------------------------
if __name__ == "__main__":
    url = "https://masothue.com/tra-cuu-ma-so-thue-theo-tinh/da-nang-35"
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    companies = parse_list_page(resp.text)
    print(f"👉 Tìm thấy {len(companies)} công ty.")
    for comp in companies:
        try:
            comp["phone"] = fetch_company_phone(comp["link"])
            print(f"{comp['tax_code']} - {comp['name']} - {comp['phone']}")
        except Exception as e:
            print(f"Lỗi lấy SĐT {comp['tax_code']}: {e}")
            comp["phone"] = ""
    save_to_google_sheet(companies,
        "https://docs.google.com/spreadsheets/d/1h_9C60cqcwOhuWS1815gIWdpYmEDjr-_Qu9COQrL7No/edit#gid=0",
        "Sheet1",
        "credentials.json")
