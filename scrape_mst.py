import re
import time
import random
import requests
from bs4 import BeautifulSoup
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json
import os
import unicodedata


# ----------------------------
# 1. Cấu hình cơ bản
# ----------------------------
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
}
BASE_URL = "https://masothue.com"

SKIP_REGEX = re.compile(r"(van\s*phong|chi\s*nhanh)", re.IGNORECASE)

def normalize_text(s: str) -> str:
    s = (s or "").strip().lower()

    # bỏ dấu tiếng Việt
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")

    # mọi ký tự lạ -> space
    s = re.sub(r"[^a-z0-9]+", " ", s)

    # gộp khoảng trắng
    s = re.sub(r"\s+", " ", s).strip()
    return s

def should_skip_company(name: str) -> bool:
    n = normalize_text(name)
    return SKIP_REGEX.search(n) is not None

# ----------------------------
# 2. Cào danh sách công ty
# ----------------------------
def parse_list_page(html: str):
    soup = BeautifulSoup(html, "lxml")
    results = []
    for block in soup.select("div.tax-listing div[data-prefetch]"):
        h3a = block.select_one("h3 > a")
        company_name = h3a.get_text(strip=True) if h3a else ""
        if company_name and should_skip_company(company_name):
            continue
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

def fetch_all_companies_from_page(page_num):
    """Lấy danh sách công ty từ một page cụ thể"""
    url = f"https://masothue.com/tra-cuu-ma-so-thue-theo-tinh/da-nang-35?page={page_num}"
    try:
        print(f"Đang crawl page {page_num}...")
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        companies = parse_list_page(resp.text)
        print(f"  Tìm thấy {len(companies)} công ty trên page {page_num}")
        time.sleep(random.uniform(1, 2))  # Delay giữa các page
        return companies
    except Exception as e:
        print(f"Lỗi khi crawl page {page_num}: {e}")
        return []

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
        print(f"Lỗi khi lấy chi tiết {path}: {e}")
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
            "Mã số thuế", 
            "Số điện thoại", 
            "Người đại diện",      # <-- Vị trí mới
            "Tên doanh nghiệp", 
            "Ngày hoạt động", 
            "Ngày cập nhật", 
            "Địa chỉ"
        ])

    # Lấy toàn bộ cột "Mã số thuế" để kiểm tra trùng lặp
    existing_tax_codes = set(tc.strip() for tc in sheet.col_values(3)[1:] if tc.strip())

    new_rows = []
    for row in data:
        if should_skip_company(row["name"]):
            continue
        tax_code = row["tax_code"].strip()
        if tax_code not in existing_tax_codes:
            new_row = [
                row["name"],
                tax_code,
                row.get("phone", ""),
                row.get("representative", ""),
                row.get("active_date", ""),
                row.get("last_update", ""),
                row.get("address", "")
            ]
            new_rows.append(new_row)

    # Thêm dòng mới lên đầu (sau tiêu đề)
    if new_rows:
        for row in reversed(new_rows):
            sheet.insert_row(row, index=2)
        print(f"Đã thêm {len(new_rows)} công ty mới vào Google Sheet")
    else:
        print("Không có công ty mới để thêm vào Google Sheet")

# ----------------------------
# 5. Chạy chính
# ----------------------------
if __name__ == "__main__":
    # NHẬP KHOẢNG TRANG CẦN CRAWL
    start_page = 1
    end_page = 7
    
    all_companies = []
    
    # Crawl từ start_page đến end_page
    for page in range(start_page, end_page + 1):
        companies_on_page = fetch_all_companies_from_page(page)
        all_companies.extend(companies_on_page)
        print(f"Hoàn thành page {page}/{end_page}\n")
    
    print(f"\nTổng số công ty thu thập được: {len(all_companies)}")
    print("Bắt đầu lấy chi tiết từng công ty...\n")
    
    # Lấy chi tiết cho từng công ty
    for idx, comp in enumerate(all_companies, 1):
        print(f"Đang xử lý {idx}/{len(all_companies)}: {comp['name']}")
        phone, rep, active_date, last_update, address = fetch_company_details(comp["link"])
        comp["phone"] = phone
        comp["representative"] = rep
        comp["active_date"] = active_date
        comp["last_update"] = last_update
        comp["address"] = address
        print(f"  MST: {comp['tax_code']} | Đại diện: {rep} | ĐT: {phone}")
    
    # Lưu vào Google Sheet
    print("\nĐang lưu dữ liệu vào Google Sheet...")
    save_to_google_sheet(all_companies,
        "https://docs.google.com/spreadsheets/d/1BVtCQdRwuswW812yCF918iKyb5l5A9PKPWZi8VZt_Io/edit?gid=0#gid=0",
        "Sheet1")
    
    print("Hoàn thành!")
