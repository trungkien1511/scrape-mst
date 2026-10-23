import re
import time
import random
import requests
from bs4 import BeautifulSoup
import os
import unicodedata
from datetime import datetime
from dotenv import load_dotenv

# Load biến môi trường
load_dotenv()

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
    inner = [
        'Phường Hải Châu', 'Phường Thanh Khê', 'Phường Sơn Trà',
        'Phường Ngũ Hành Sơn', 'Phường Liên Chiểu', 'Phường Cẩm Lệ',
        'Phường Hòa Cường', 'Phường Hòa Xuân', 'Phường An Khê',
        'Phường An Hải', 'Phường Hòa Khánh', 'Phường Hải Vân'
    ]
    suburban = ['Xã Bà Nà', 'Xã Hòa Tiến', 'Xã Hòa Vang']
    nearby = [
        'Phường Điện Bàn', 'Phường Điện Bàn Đông', 'Phường Điện Bàn Tây',
        'Phường Điện Bàn Bắc', 'Phường Bàn Thạch', 'Phường An Thắng',
        'Phường Hội An', 'Phường Hội An Đông', 'Phường Hội An Tây'
    ]
    
    for kw in inner:
        if kw in address: return "inner_da_nang" 
    for kw in suburban:
        if kw in address: return "suburban_da_nang"
    for kw in nearby:
        if kw in address: return "near_da_nang"
    return "other"

# ----------------------------
# 3. Hàm giao tiếp Turso (HTTP API)
# ----------------------------
def execute_turso(url, token, sql, args):
    hrana_args = []
    for arg in args:
        if arg is None:
            hrana_args.append({"type": "null"})
        else:
            hrana_args.append({"type": "text", "value": str(arg)})
            
    payload = {
        "requests": [
            {"type": "execute", "stmt": {"sql": sql, "args": hrana_args}},
            {"type": "close"}
        ]
    }
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    
    response = requests.post(f"{url}/v2/pipeline", headers=headers, json=payload)
    if response.status_code != 200:
        raise Exception(f"Lỗi HTTP {response.status_code}: {response.text}")
        
    data = response.json()
    result = data.get("results", [])[0]
    if result.get("type") == "error":
        raise Exception(f"Lỗi SQL: {result.get('error', {}).get('message')}")
    return result

def parse_vn_date(s: str):
    s = str(s).strip()
    if not s or s == "None": return None
    if "-" in s and len(s) > 8:
        return s.split(" ")[0] 
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None

def clean_phone(phone_str):
    phone = str(phone_str).strip()
    if phone.endswith(".0"): phone = phone[:-2]
    if phone and len(phone) == 9 and phone.isdigit(): phone = "0" + phone
    return phone

# ----------------------------
# 4. Hàm lưu vào Turso
# ----------------------------
def save_to_turso(data):
    print("\n⏳ Đang kết nối Turso...")
    turso_url = os.environ.get("TURSO_DATABASE_URL", "").replace("libsql://", "https://")
    turso_token = os.environ.get("TURSO_AUTH_TOKEN", "")
    
    if not turso_url or not turso_token:
        print("❌ Lỗi: Thiếu TURSO_DATABASE_URL hoặc TURSO_AUTH_TOKEN")
        return

    success = 0
    for i, row in enumerate(data, start=1):
        if should_skip_company(row["name"]):
            continue
            
        tax_code = row["tax_code"].strip()
        if not tax_code:
            continue

        phone = clean_phone(row.get("phone", ""))
        representative = row.get("representative", "").strip()
        address = row.get("address", "").strip()
        active_date = parse_vn_date(row.get("active_date", ""))
        source_updated_at = parse_vn_date(row.get("last_update", ""))
        location_category = classify_location(address)

        # Chuyển rỗng thành None để tránh ghi đè dữ liệu cũ bằng chuỗi rỗng
        db_phone = phone if phone else None
        db_rep = representative if representative else None
        db_address = address if address else None
        db_active_date = active_date if active_date else None
        db_source_updated_at = source_updated_at if source_updated_at else None

        sql = """INSERT INTO companies 
                   (company_name, tax_code, phone, representative, active_date, 
                    source_updated_at, address, location_category, source_url)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                 ON CONFLICT(tax_code) DO UPDATE SET
                   company_name      = excluded.company_name,
                   phone             = COALESCE(excluded.phone, companies.phone),
                   representative    = COALESCE(excluded.representative, companies.representative),
                   active_date       = COALESCE(excluded.active_date, companies.active_date),
                   source_updated_at = COALESCE(excluded.source_updated_at, companies.source_updated_at),
                   address           = COALESCE(excluded.address, companies.address),
                   location_category = excluded.location_category,
                   updated_at        = datetime('now')"""
                 
        args = [row["name"], tax_code, db_phone, db_rep, 
                db_active_date, db_source_updated_at, db_address, location_category, BASE_URL + row["link"]]

        try:
            execute_turso(turso_url, turso_token, sql, args)
            success += 1
            print(f"  [{i}/{len(data)}] Đã lưu: {tax_code} - {row['name']} | {location_category}")
        except Exception as e:
            print(f"  ❌ Lỗi dòng {i} ({tax_code}): {e}")

    print(f"\n🎉 HOÀN TẤT! Đã nạp thành công {success} công ty vào Turso.")

# ----------------------------
# 5. Các hàm crawl dữ liệu
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
            if active_tr: active_tr = active_tr.find_parent("tr")
        if active_tr:
            span = active_tr.find("span", class_="copy")
            if span: active_date = span.get_text(strip=True)

        last_update = ""
        update_td = soup.find("td", colspan="2")
        if update_td and "Cập nhật mã số thuế" in update_td.get_text():
            em_tag = update_td.find("em")
            if em_tag: last_update = em_tag.get_text(strip=True)

        addr_td = soup.select_one("td[itemprop='address'] span.copy")
        address = addr_td.get_text(strip=True) if addr_td else ""

        time.sleep(random.uniform(1.2, 2.5))
        return phone, representative, active_date, last_update, address

    except Exception as e:
        print(f"Lỗi khi lấy chi tiết {path}: {e}")
        return "", "", "", "", ""

# ----------------------------
# 6. Chạy chính
# ----------------------------
if __name__ == "__main__":
    start_page = 1
    end_page = 1
    
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
    
    # 🔥 ĐẢO NGƯỢC MẢNG TẠI ĐÂY: 
    # Công ty mới nhất (trên cùng web) sẽ được đẩy xuống cuối mảng để nạp sau cùng
    all_companies.reverse()

    print("\nĐang đẩy dữ liệu trực tiếp lên Turso...")
    save_to_turso(all_companies)