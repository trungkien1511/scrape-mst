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
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def should_skip_company(name: str) -> bool:
    n = normalize_text(name)
    return SKIP_REGEX.search(n) is not None

# ----------------------------
# 2. Hàm phân loại địa chỉ
# ----------------------------
def classify_location(address):
    """
    Phân loại địa chỉ:
    - TRONG_DA_NANG: Các quận nội thành
    - NGOAI_THANH_DA_NANG: Huyện Hòa Vang
    - GAN_DA_NANG: Các khu vực giáp ranh (Điện Bàn, Hội An, Bàn Thạch, An Thắng)
    - KHAC: Tất cả các trường hợp còn lại (bao gồm Duy Xuyên và các khu vực khác)
    """
    
    # Trong Đà Nẵng (nội thành)
    inner = [
        'Phường Hải Châu', 'Phường Thanh Khê', 'Phường Sơn Trà',
        'Phường Ngũ Hành Sơn', 'Phường Liên Chiểu', 'Phường Cẩm Lệ',
        'Phường Hòa Cường', 'Phường Hòa Xuân', 'Phường An Khê',
        'Phường An Hải', 'Phường Hòa Khánh', 'Phường Hải Vân'
    ]
    
    # Ngoại thành Đà Nẵng (Hòa Vang)
    suburban = ['Xã Bà Nà', 'Xã Hòa Tiến', 'Xã Hòa Vang']
    
    # Gần Đà Nẵng (Quảng Nam giáp ranh)
    nearby = [
        'Phường Điện Bàn', 'Phường Điện Bàn Đông', 'Phường Điện Bàn Tây',
        'Phường Điện Bàn Bắc', 'Phường An Thắng',
        'Phường Hội An', 'Phường Hội An Đông', 'Phường Hội An Tây'
    ]
    
    # Kiểm tra lần lượt
    for kw in inner:
        if kw in address:
            return "TRONG_DA_NANG"
    
    for kw in suburban:
        if kw in address:
            return "NGOAI_THANH_DA_NANG"
    
    for kw in nearby:
        if kw in address:
            return "GAN_DA_NANG"
    
    # Tất cả các trường hợp còn lại (bao gồm Duy Xuyên và các xã khác)
    return "KHAC"

# ----------------------------
# 3. Lưu vào Google Sheet (2 sheet: Sheet1 và Sheet2)
# ----------------------------
def save_to_google_sheets(data, sheet_url, sheet_name_main="Sheet1", sheet_name_other="Sheet2"):
    """
    Lưu dữ liệu vào 2 sheet với thứ tự cột:
    Tên Công Ty | Mã Số Thuế | Số Điện Thoại | Người đại diện | Ngày Cấp | Ngày Cập Nhật | Địa Chỉ | Phân Loại
    
    - Sheet1: TRONG_DA_NANG + NGOAI_THANH_DA_NANG + GAN_DA_NANG
    - Sheet2: KHAC (bao gồm Duy Xuyên và các khu vực khác)
    """
    scope = ["https://spreadsheets.google.com/feeds",
             "https://www.googleapis.com/auth/drive"]
    creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_url(sheet_url)
    
    # Lấy hoặc tạo 2 sheet
    try:
        main_sheet = spreadsheet.worksheet(sheet_name_main)
    except gspread.exceptions.WorksheetNotFound:
        main_sheet = spreadsheet.add_worksheet(title=sheet_name_main, rows=100, cols=10)
    
    try:
        other_sheet = spreadsheet.worksheet(sheet_name_other)
    except gspread.exceptions.WorksheetNotFound:
        other_sheet = spreadsheet.add_worksheet(title=sheet_name_other, rows=100, cols=10)
    
    # Header: Tên | MST | SĐT | Đại diện | Ngày Cấp | Ngày Cập Nhật | Địa Chỉ | Phân Loại
    header = [
        "Tên Công Ty",
        "Mã Số Thuế",
        "Số Điện Thoại",
        "Người đại diện",
        "Ngày Cấp",
        "Ngày Cập Nhật",
        "Địa Chỉ",
        "Phân Loại"
    ]
    
    if main_sheet.row_count == 0 or not main_sheet.row_values(1):
        main_sheet.append_row(header)
    
    if other_sheet.row_count == 0 or not other_sheet.row_values(1):
        other_sheet.append_row(header)
    
    # Kiểm tra trùng MST (cột 2)
    existing_main_tax = set(tc.strip() for tc in main_sheet.col_values(2)[1:] if tc.strip())
    existing_other_tax = set(tc.strip() for tc in other_sheet.col_values(2)[1:] if tc.strip())
    
    main_rows = []
    other_rows = []
    
    for row in data:
        if should_skip_company(row["name"]):
            continue
        
        tax_code = row["tax_code"].strip()
        address = row.get("address", "")
        
        # Phân loại địa chỉ
        location_type = classify_location(address)
        
        # Thứ tự cột: Tên | MST | SĐT | Đại diện | Ngày Cấp | Ngày Cập Nhật | Địa Chỉ | Phân Loại
        new_row = [
            row["name"],                     # Tên Công Ty
            tax_code,                        # Mã Số Thuế
            row.get("phone", ""),            # Số Điện Thoại
            row.get("representative", ""),   # Người đại diện
            row.get("active_date", ""),      # Ngày Cấp
            row.get("last_update", ""),      # Ngày Cập Nhật
            address,                         # Địa Chỉ
            location_type                    # Phân Loại
        ]
        
        # Phân loại vào sheet tương ứng
        if location_type in ["TRONG_DA_NANG", "NGOAI_THANH_DA_NANG", "GAN_DA_NANG"]:
            if tax_code not in existing_main_tax:
                main_rows.append(new_row)
                existing_main_tax.add(tax_code)
        else:  # KHAC (bao gồm Duy Xuyên và tất cả các khu vực khác)
            if tax_code not in existing_other_tax:
                other_rows.append(new_row)
                existing_other_tax.add(tax_code)
    
    # Ghi vào main sheet (Sheet1)
    if main_rows:
        main_sheet.append_rows(main_rows)
        print(f"Đã thêm {len(main_rows)} công ty vào sheet '{sheet_name_main}'")
    else:
        print(f"Không có công ty mới để thêm vào sheet '{sheet_name_main}'")
    
    # Ghi vào other sheet (Sheet2)
    if other_rows:
        other_sheet.append_rows(other_rows)
        print(f"Đã thêm {len(other_rows)} công ty vào sheet '{sheet_name_other}'")
    else:
        print(f"Không có công ty mới để thêm vào sheet '{sheet_name_other}'")
    
    return len(main_rows), len(other_rows)

# ----------------------------
# 4. Các hàm crawl dữ liệu (giữ nguyên)
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
    url = f"https://masothue.com/tra-cuu-ma-so-thue-theo-tinh/da-nang-35?page={page_num}"
    try:
        print(f"Đang crawl page {page_num}...")
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        companies = parse_list_page(resp.text)
        print(f"  Tìm thấy {len(companies)} công ty trên page {page_num}")
        time.sleep(random.uniform(1, 2))
        return companies
    except Exception as e:
        print(f"Lỗi khi crawl page {page_num}: {e}")
        return []

def fetch_company_details(path: str):
    url = BASE_URL + path
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        phone_td = soup.select_one("td[itemprop='telephone'] span.copy")
        phone = phone_td.get_text(strip=True) if phone_td else ""

        rep_td = soup.select_one("tr[itemprop='alumni'] span[itemprop='name']")
        representative = rep_td.get_text(strip=True) if rep_td else ""

        active_date = ""
        active_tr = soup.find("tr", string=lambda t: t and "Ngày hoạt động" in t)
        if not active_tr:
            active_tr = soup.find("i", class_="fa fa-calendar")
            if active_tr:
                active_tr = active_tr.find_parent("tr")
        if active_tr:
            span = active_tr.find("span", class_="copy")
            if span:
                active_date = span.get_text(strip=True)

        last_update = ""
        update_td = soup.find("td", colspan="2")
        if update_td and "Cập nhật mã số thuế" in update_td.get_text():
            em_tag = update_td.find("em")
            if em_tag:
                last_update = em_tag.get_text(strip=True)

        addr_td = soup.select_one("td[itemprop='address'] span.copy")
        address = addr_td.get_text(strip=True) if addr_td else ""

        time.sleep(random.uniform(1.2, 2.5))
        return phone, representative, active_date, last_update, address

    except Exception as e:
        print(f"Lỗi khi lấy chi tiết {path}: {e}")
        return "", "", "", "", ""

# ----------------------------
# 5. Chạy chính
# ----------------------------
if __name__ == "__main__":
    start_page = 1
    end_page = 5
    
    all_companies = []
    
    for page in range(start_page, end_page + 1):
        companies_on_page = fetch_all_companies_from_page(page)
        all_companies.extend(companies_on_page)
        print(f"Hoàn thành page {page}/{end_page}\n")
    
    print(f"\nTổng số công ty thu thập được: {len(all_companies)}")
    print("Bắt đầu lấy chi tiết từng công ty...\n")
    
    for idx, comp in enumerate(all_companies, 1):
        print(f"Đang xử lý {idx}/{len(all_companies)}: {comp['name']}")
        phone, rep, active_date, last_update, address = fetch_company_details(comp["link"])
        comp["phone"] = phone
        comp["representative"] = rep
        comp["active_date"] = active_date
        comp["last_update"] = last_update
        comp["address"] = address
        print(f"  MST: {comp['tax_code']} | Đại diện: {rep} | ĐT: {phone}")
    
    print("\nĐang lưu dữ liệu vào Google Sheet...")
    main_count, other_count = save_to_google_sheets(
        all_companies,
        "https://docs.google.com/spreadsheets/d/1BVtCQdRwuswW812yCF918iKyb5l5A9PKPWZi8VZt_Io/edit?gid=0#gid=0",
        "Sheet2",  # Sheet 1: TRONG_DA_NANG + NGOAI_THANH_DA_NANG + GAN_DA_NANG
        "Sheet3"   # Sheet 2: KHAC (bao gồm Duy Xuyên và các khu vực khác)
    )
    
    print(f"\n✅ Hoàn thành!")
    print(f"   - Sheet 'Sheet1': {main_count} công ty mới")
    print(f"   - Sheet 'Sheet2': {other_count} công ty mới")
