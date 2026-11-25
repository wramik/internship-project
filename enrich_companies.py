import argparse
import json
import os
import re
import time
import psycopg2
import requests

from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

from web_service import get_db_connection

DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

MAX_TEXT_CHARS = 8000
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

def get_companies(company_name=None, limit=None, status=None):

    connection = get_db_connection()
    cursor = connection.cursor()

    try:
        query = """
            SELECT id, company_name, web, country, postcode,
                   city, street, house_no
            FROM companies
        """

        conditions = []
        params = []

        if company_name:
            conditions.append("company_name = %s")
            params.append(company_name)

        elif status is not None:
            conditions.append("status = %s")
            params.append(status)

        else:
            conditions.append("(status IS NULL OR status <> 0)")

        query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY id"

        if limit is not None:
            query += " LIMIT %s"
            params.append(limit)

        cursor.execute(query, params)

        return cursor.fetchall()

    finally:
        cursor.close()
        connection.close()

def normalize_url(url):

    if not url:
        return None

    url = url.strip()

    parsed_url = urlparse(url)
    if not parsed_url.scheme:
        url = "https://" + url

    return url

def fetch_page(url):

    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        return response.text

    except requests.RequestException as error:
        print(f"Website request failed for {url}: {error}")
        return None

def find_impressum_url(html, base_url):

    soup = BeautifulSoup(html, "html.parser")

    for link in soup.find_all("a", href=True):
        link_text = link.get_text(strip=True).lower()

        if link_text in ("impressum", "imprint"):
            return urljoin(base_url, link["href"])

    for link in soup.find_all("a", href=True):
        href = link["href"].lower()

        if "impressum" in href or "imprint" in href:
            return urljoin(base_url, link["href"])

    return None

def extract_page_text(html):

    soup = BeautifulSoup(html, "html.parser")

    for element in soup([
        "script",
        "style",
        "noscript",
        "svg",
        "nav",
        "header",
        "footer",
        "form"
    ]):
        element.decompose()

    text = soup.get_text(separator=" ", strip=True)

    text = re.sub(r"\s+", " ", text)

    return text.strip()

def optimize_impressum_text(text):

    if len(text) <= MAX_TEXT_CHARS:
        return text

    head_pattern = re.compile(
        r"impressum|angaben gemäß|anbieter|diensteanbieter",
        re.IGNORECASE
    )

    tail_pattern = re.compile(
        r"\+49|0049|DE\d{9}|"
        r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}|"
        r"\bHRB\b|\bHRA\b|Registergericht|Amtsgericht|"
        r"verantwortlich",
        re.IGNORECASE
    )

    head_match = head_pattern.search(text)

    tail_matches = list(tail_pattern.finditer(text))

    if not head_match or not tail_matches:
        half = (MAX_TEXT_CHARS - 5) // 2
        return text[:half] + " ... " + text[-half:]

    start = max(0, head_match.start() - 500)

    end = min(len(text), tail_matches[-1].end() + 400)

    optimized_text = text[start:end]

    if len(optimized_text) > MAX_TEXT_CHARS:
        half = (MAX_TEXT_CHARS - 5) // 2
        optimized_text = (
            optimized_text[:half]
            + " ... "
            + optimized_text[-half:]
        )

    return optimized_text

def extract_legal_data(text, company_name):
    api_key = os.getenv("DEEPSEEK_API_KEY")

    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not configured")

    system_prompt = """
    You extract legally required company information ("Impressum" / "Imprint")
    from German-language website text.

    Return ONLY a valid JSON object (no markdown fences, no commentary)
    that parses with json.loads. The JSON must have exactly these keys:

    {
        "company_name": null,
        "postal_address": {
            "street": null,
            "house_no": null,
            "postcode": null,
            "city": null,
            "country": null
        },
        "handelsregister": null,
        "vat_number": null,
        "phone": null,
        "email": null,
        "responsible_person": null
    }

    Rules:
    - Extract ONLY information actually present in the text.
    - Use null when information is absent.
    - Do not invent or guess values.
    - Keep phone in international format when possible.
    - German VAT IDs look like DE followed by 9 digits.
    - Email must be lowercase.
    - responsible_person is the named person or role legally responsible
      for the content.
    - Output valid JSON only.
    """

    for attempt in range(MAX_RETRIES):
        try:
            response = requests.post(
                DEEPSEEK_API_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": DEEPSEEK_MODEL,
                    "messages": [
                        {
                            "role": "system",
                            "content": system_prompt,
                        },
                        {
                            "role": "user",
                            "content": (
                                f"Company name: {company_name}\n\n"
                                f"Impressum text:\n{text}\n\n"
                                "Return the requested information as JSON."
                            ),
                        },
                    ],
                    "response_format": {"type": "json_object"},
                    "max_tokens": 1024,
                    "temperature": 0,
                },
                timeout=REQUEST_TIMEOUT,
            )

            response.raise_for_status()

            result = response.json()
            content = result["choices"][0]["message"]["content"]

            if not content or not content.strip():
                raise ValueError("DeepSeek returned an empty response")

            legal_data = json.loads(content)

            if not isinstance(legal_data, dict):
                raise ValueError("DeepSeek response is not a JSON object")

            required_fields = {
                "company_name",
                "postal_address",
                "handelsregister",
                "vat_number",
                "phone",
                "email",
                "responsible_person",
            }

            if not required_fields.issubset(legal_data):
                raise ValueError("DeepSeek response is missing required fields")

            if not isinstance(legal_data["postal_address"], dict):
                raise ValueError("postal_address must be a JSON object")

            address_fields = {
                "street",
                "house_no",
                "postcode",
                "city",
                "country",
            }

            if not address_fields.issubset(legal_data["postal_address"]):
                raise ValueError("postal_address is missing required fields")

            return legal_data

        except (requests.RequestException, ValueError, KeyError, IndexError) as error:
            print(
                f"DeepSeek attempt {attempt + 1}/{MAX_RETRIES} "
                f"failed for {company_name}: {error}"
            )

            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt)

    raise RuntimeError(
        f"DeepSeek failed to extract legal data for {company_name}"
    )

def save_company_data(company_id, legal_data, impressum_url, status, company_name=None):

    connection = get_db_connection()
    cursor = connection.cursor()

    address = legal_data.get("postal_address") or {}

    if isinstance(address, str):
        postal_address = address
        address = {}

    else:
        address_parts = [
            address.get("street"),
            address.get("house_no"),
            address.get("postcode"),
            address.get("city"),
            address.get("country"),
        ]

        postal_address = ", ".join(
            str(part) for part in address_parts if part
        ) or None

    try:
        cursor.execute(
            """
            UPDATE companies
            SET status = %s,
                impressum_url = %s,
                legal_name = %s,
                postal_address = %s,
                handelsregister = %s,
                vat_number = %s,
                phone = %s,
                email = %s,
                responsible_person = %s,
                raw_json = %s,
                updated_at = NOW()
            WHERE id = %s
            """,
            (
                status,
                impressum_url,
                legal_data.get("company_name") or company_name,
                postal_address,
                legal_data.get("handelsregister"),
                legal_data.get("vat_number"),
                legal_data.get("phone"),
                legal_data.get("email"),
                legal_data.get("responsible_person"),
                json.dumps(legal_data, ensure_ascii=False),
                company_id,
            ),
        )

        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        cursor.close()
        connection.close()


def determine_status(legal_data):

    if not legal_data:
        return 2  # PARSING_FAILED

    address = legal_data.get("postal_address") or {}

    other_fields = [
        value
        for key, value in legal_data.items()
        if key not in ("postal_address", "company_name")
    ]

    address_values = (
        list(address.values())
        if isinstance(address, dict)
        else [address]
    )

    all_values = other_fields + address_values

    has_data = any(
        value is not None and str(value).strip()
        for value in all_values
    )

    if not has_data:
        return 2  # PARSING_FAILED

    if not isinstance(address, dict):
        return 3  # ADDRESS_INCOMPLETE

    required_address_fields = (
        "postcode",
        "city",
        "street",
        "house_no",
    )

    address_complete = all(
        address.get(field) is not None
        and str(address[field]).strip()
        for field in required_address_fields
    )

    if address_complete:
        return 0  # OK

    return 3  # ADDRESS_INCOMPLETE


def process_company(company):

    #id, company_name, web, country, postcode, city, street, house_no
    company_id, company_name, web = company[:3]

    print(f"\nProcessing: {company_name}")

    if not web or not web.strip():
        print("Website is missing.")
        save_company_data(company_id, {}, None, 5)
        return 5

    website_url = normalize_url(web)

    homepage_html = fetch_page(website_url)

    if homepage_html is None:
        save_company_data(company_id, {}, None, 4)
        return 4

    impressum_url = find_impressum_url(
        homepage_html,
        website_url
    )

    if not impressum_url:
        print("Impressum link not found.")
        save_company_data(company_id, {}, None, 1)
        return 1

    print(f"Impressum found: {impressum_url}")

    impressum_html = fetch_page(impressum_url)

    if impressum_html is None:
        save_company_data(company_id, {}, impressum_url, 4)
        return 4

    impressum_text = extract_page_text(impressum_html)

    if not impressum_text:
        print("Impressum page contains no text.")
        save_company_data(company_id, {}, impressum_url, 2)
        return 2

    optimized_text = optimize_impressum_text(impressum_text)

    print(
        f"Estimated tokens: "
        f"{len(impressum_text) // 4} -> {len(optimized_text) // 4}"
    )

    print(
        f"Text length: {len(impressum_text)} -> "
        f"{len(optimized_text)} characters"
    )

    try:
        legal_data = extract_legal_data(
            optimized_text,
            company_name
        )

    except Exception as error:
        print(f"Enrichment failed: {error}")
        save_company_data(company_id, {}, impressum_url, 4)
        return 4

    status = determine_status(legal_data)

    save_company_data(
        company_id,
        legal_data,
        impressum_url,
        status,
        company_name
    )

    print(f"Finished: {company_name}, status={status}")

    return status

def main():

    parser = argparse.ArgumentParser(
        description="Enrich company data using Impressum websites and DeepSeek."
    )

    parser.add_argument(
        "--company",
        help="Process only the specified company name."
    )

    parser.add_argument(
        "--limit",
        type=int,
        help="Maximum number of companies to process."
    )

    parser.add_argument(
        "--status",
        type=int,
        help="Process only companies with the specified status."
    )

    args = parser.parse_args()

    companies = get_companies(
        company_name=args.company,
        limit=args.limit,
        status=args.status
    )

    if not companies:
        print("No companies found for processing.")
        return

    print(f"Companies to process: {len(companies)}")

    for company in companies:
        try:
            process_company(company)

        except Exception as error:
            company_id, company_name = company[:2]

            print(f"Unexpected error for {company_name}: {error}")

            try:
                save_company_data(company_id, {}, None, 4)
            except Exception as db_error:
                print(f"Failed to save error status: {db_error}")

    print("Batch processing finished.")

if __name__ == "__main__":
    main()