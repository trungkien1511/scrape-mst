import re
import time
import random
import requests
from bs4 import BeautifulSoup
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json
import os

# ----------------------------
# 1. Cấu hình cơ bản
# ----------------------------
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
}
BASE_URL = "https://masothue.com"


# ----------------------------
# 2. Cào danh sách công ty
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
# 3. Lấy chi tiết công ty
# ----------------------------
def fetch_company_details(path: str):
    url = BASE_URL + path
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        # Số điện thoại
        phone_td = soup.select_one("td[itemprop='telephone'] span.copy")
        phone = phone_td.get_text(strip=True) if phone_td else ""

        # Người đại diện
        rep_td = soup.select_one("tr[itemprop='alumni'] span[itemprop='name']")
        representative = rep_td.get_text(strip=True) if rep_td else ""

        # Ngày hoạt động
        # Ngày hoạt động
        active_date = ""
        active_tr = soup.find("tr", string=lambda t: t and "Ngày hoạt động" in t)
        if not active_tr:
            # Nếu không khớp trực tiếp, tìm qua icon fa-calendar
            active_tr = soup.find("i", class_="fa fa-calendar")
            if active_tr:
                active_tr = active_tr.find_parent("tr")
        if active_tr:
            span = active_tr.find("span", class_="copy")
            if span:
                active_date = span.get_text(strip=True)

        # Ngày cập nhật MST
        last_update = ""
        update_td = soup.find("td", colspan="2")
        if update_td and "Cập nhật mã số thuế" in update_td.get_text():
            em_tag = update_td.find("em")
            if em_tag:
                last_update = em_tag.get_text(strip=True)

        # Địa chỉ
        addr_td = soup.select_one("td[itemprop='address'] span.copy")
        address = addr_td.get_text(strip=True) if addr_td else ""

        # Delay nhẹ để tránh bị chặn
        time.sleep(random.uniform(1.2, 2.5))
        return phone, representative, active_date, last_update, address

    except Exception as e:
        return "", "", "", "", ""


# ----------------------------
# 4. Lưu Google Sheet (giữ lịch sử + không trùng MST)
# ----------------------------
def save_to_google_sheet(data, sheet_url, sheet_name="Sheet1"):
    scope = ["https://spreadsheets.google.com/feeds",
             "https://www.googleapis.com/auth/drive"]
    creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_url(sheet_url).worksheet(sheet_name)

    # Nếu sheet trống -> thêm tiêu đề
    if sheet.row_count == 0 or not sheet.row_values(1):
        sheet.append_row([
            "Tên doanh nghiệp", "Người đại diện",
            "Mã số thuế", "Số điện thoại", "Ngày hoạt động",
            "Ngày cập nhật", "Địa chỉ"
        ])

    # Lấy toàn bộ cột "Mã số thuế" để kiểm tra trùng lặp
    existing_tax_codes = set(tc.strip() for tc in sheet.col_values(3)[1:] if tc.strip())

    new_rows = []
    for row in data:
        tax_code = row["tax_code"].strip()
        if tax_code not in existing_tax_codes:
            new_row = [
                row["name"],
                row.get("representative", ""),
                tax_code,
                row.get("phone", ""),
                row.get("active_date", ""),
                row.get("last_update", ""),
                row.get("address", "")
            ]
            new_rows.append(new_row)

    # Thêm dòng mới lên đầu (sau tiêu đề)
    if new_rows:
        for row in reversed(new_rows):
            sheet.insert_row(row, index=2)

# ----------------------------
# 5. Chạy chính
# ----------------------------
if __name__ == "__main__":
    url = "https://masothue.com/tra-cuu-ma-so-thue-theo-tinh/da-nang-35"
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()

    companies = parse_list_page(resp.text)

    for comp in companies:
        phone, rep, active_date, last_update, address = fetch_company_details(comp["link"])
        comp["phone"] = phone
        comp["representative"] = rep
        comp["active_date"] = active_date
        comp["last_update"] = last_update
        comp["address"] = address
        print(f"{comp['tax_code']} | {comp['name']} | {rep} | {phone} | {active_date} | {last_update} | {address}")

    save_to_google_sheet(companies,
        "https://docs.google.com/spreadsheets/d/1BVtCQdRwuswW812yCF918iKyb5l5A9PKPWZi8VZt_Io/edit?gid=0#gid=0",
        "Sheet1")




