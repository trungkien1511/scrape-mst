import re
import time
import requests
from bs4 import BeautifulSoup
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json
import os

# ----------------------------
# Cấu hình
# ----------------------------
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
}
BASE_URL = "https://masothue.com"
TARGET_URL = "https://masothue.com/tra-cuu-ma-so-thue-theo-tinh/da-nang-35"
SHEET_URL = "https://docs.google.com/spreadsheets/d/1h_9C60cqcwOhuWS1815gIWdpYmEDjr-_Qu9COQrL7No/edit#gid=0"
SHEET_NAME = "Sheet1"

# ----------------------------
# 1. Cào danh sách doanh nghiệp
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

# ----------------------------
# 2. Cào chi tiết công ty
# ----------------------------
def fetch_company_details(path: str, delay: float = 1.5) -> dict:
    url = BASE_URL + path
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    # --- lấy số điện thoại ---
    phone_td = soup.select_one("td[itemprop='telephone'] span.copy")
    phone = phone_td.get_text(strip=True) if phone_td else ""

    # --- lấy ngày cập nhật MST ---
    last_update = ""
    update_td = soup.find("td", colspan="2")  # <td colspan="2"> chứa ngày cập nhật
    if update_td and "Cập nhật mã số thuế" in update_td.get_text():
        em_tag = update_td.find("em")
        if em_tag:
            last_update = em_tag.get_text(strip=True)

    # --- lấy người đại diện ---
    representative = ""
    rep_tr = soup.find("tr", {"itemprop": "alumni"})
    if rep_tr:
        rep_span = rep_tr.find("span", {"itemprop": "name"})
        if rep_span:
            representative = rep_span.get_text(strip=True)

    time.sleep(delay)  # tránh bị chặn
    return {
        "phone": phone,
        "last_update": last_update,
        "representative": representative
    }

# ----------------------------
# 3. Lưu Google Sheet
# ----------------------------
def save_to_google_sheet(data, sheet_url, sheet_name="Sheet1"):
    scope = ["https://spreadsheets.google.com/feeds", 
             "https://www.googleapis.com/auth/drive"]
    creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_url(sheet_url).worksheet(sheet_name)
    sheet.clear()
    sheet.append_row(["Tên doanh nghiệp", "Người đại diện", "Mã số thuế", "Số điện thoại", "Ngày cập nhật MST"])
    for row in data:
        sheet.append_row([
            row["name"], 
            row.get("representative", ""),
            row["tax_code"], 
            row.get("phone", ""), 
            row.get("last_update", "")
        ])
    print("✔ Đã lưu dữ liệu vào Google Sheet!")

# ----------------------------
# 4. Main
# ----------------------------
if __name__ == "__main__":
    print("🔎 Đang tải danh sách công ty...")
    resp = requests.get(TARGET_URL, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    companies = parse_list_page(resp.text)
    print(f"👉 Tìm thấy {len(companies)} công ty.")

    for comp in companies:
        try:
            details = fetch_company_details(comp["link"])
            comp["phone"] = details["phone"]
            comp["last_update"] = details["last_update"]
            comp["representative"] = details["representative"]
            print(f"{comp['tax_code']} | {comp['name']} | {comp['representative']} | {comp['phone']} | {comp['last_update']}")
        except Exception as e:
            print(f"⚠ Lỗi lấy chi tiết {comp['tax_code']}: {e}")
            comp["phone"] = ""
            comp["last_update"] = ""
            comp["representative"] = ""

    save_to_google_sheet(companies, SHEET_URL, SHEET_NAME)
