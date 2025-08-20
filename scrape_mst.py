import re
import time
import requests
from bs4 import BeautifulSoup
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json
import os

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
}
BASE_URL = "https://masothue.com"


# ----------------------------
# 1. Cào danh sách công ty
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
# 2. Lấy chi tiết công ty
# ----------------------------
def fetch_company_details(path: str, delay: float = 1.5):
    url = BASE_URL + path
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    # Số điện thoại
    phone_td = soup.select_one("td[itemprop='telephone'] span.copy")
    phone = phone_td.get_text(strip=True) if phone_td else ""

    # Người đại diện
    rep_td = soup.select_one("tr[itemprop='alumni'] span[itemprop='name']")
    representative = rep_td.get_text(strip=True) if rep_td else ""

    # Ngày cập nhật MST
    last_update = ""
    update_td = soup.find("td", colspan="2")
    if update_td and "Cập nhật mã số thuế" in update_td.get_text():
        em_tag = update_td.find("em")
        if em_tag:
            last_update = em_tag.get_text(strip=True)

    time.sleep(delay)
    return phone, representative, last_update


# ----------------------------
# 3. Lưu Google Sheet (giữ lịch sử + version mới)
# ----------------------------
def save_to_google_sheet(data, sheet_url, sheet_name="Sheet1"):
    scope = ["https://spreadsheets.google.com/feeds",
             "https://www.googleapis.com/auth/drive"]
    creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_url(sheet_url).worksheet(sheet_name)

    # Lấy toàn bộ cột "Mã số thuế" để kiểm tra trùng lặp
    existing_tax_codes = set(tc.strip() for tc in sheet.col_values(3)[1:] if tc.strip())  
    # cột 3 = "Mã số thuế", bỏ hàng tiêu đề

    # Nếu sheet trống -> thêm tiêu đề
    if sheet.row_count == 0 or not sheet.row_values(1):
        sheet.append_row(["Tên doanh nghiệp", "Người đại diện",
                          "Mã số thuế", "Số điện thoại", "Ngày cập nhật"])
        existing_tax_codes = set()

    new_rows = []
    for row in data:
        tax_code = row["tax_code"].strip()
        if tax_code not in existing_tax_codes:  # chỉ thêm nếu chưa có
            new_row = [row["name"], row.get("representative", ""),
                       tax_code, row.get("phone", ""), row.get("last_update", "")]
            new_rows.append(new_row)

    # Thêm dòng mới ngay dưới tiêu đề (index=2)
    if new_rows:
        for row in reversed(new_rows):
            sheet.insert_row(row, index=2)

    print(f"✔ Đã thêm {len(new_rows)} dòng mới vào Google Sheet!")


# ----------------------------
# 4. Chạy chính
# ----------------------------
if __name__ == "__main__":
    url = "https://masothue.com/tra-cuu-ma-so-thue-theo-tinh/da-nang-35"
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    companies = parse_list_page(resp.text)
    print(f"👉 Tìm thấy {len(companies)} công ty.")

    for comp in companies:
        try:
            phone, rep, last_update = fetch_company_details(comp["link"])
            comp["phone"] = phone
            comp["representative"] = rep
            comp["last_update"] = last_update
            print(f"{comp['tax_code']} - {comp['name']} - {rep} - {phone} - {last_update}")
        except Exception as e:
            print(f"Lỗi lấy chi tiết {comp['tax_code']}: {e}")
            comp["phone"] = ""
            comp["representative"] = ""
            comp["last_update"] = ""

    save_to_google_sheet(companies,
        "https://docs.google.com/spreadsheets/d/1h_9C60cqcwOhuWS1815gIWdpYmEDjr-_Qu9COQrL7No/edit#gid=0",
        "Sheet1")


