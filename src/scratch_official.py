import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse

headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
sample_urls = [
    "https://greatyop.com/bourse-sophie-germain-france/",
    "https://scholarships365.info/university-of-oulu-scholarships/"
]

def find_official_link(article_url: str):
    print(f"\n=== Article : {article_url} ===")
    resp = requests.get(article_url, headers=headers, timeout=15)
    soup = BeautifulSoup(resp.text, "html.parser")
    src_domain = urlparse(article_url).netloc

    candidates = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        text = a.get_text(strip=True)
        t_low = text.lower()
        h_low = href.lower()
        parsed = urlparse(href)

        # Exclure liens internes ou réseaux sociaux
        if not parsed.netloc or parsed.netloc == src_domain:
            continue
        if any(social in parsed.netloc for social in ["facebook", "twitter", "x.com", "instagram", "linkedin", "t.me", "telegram", "whatsapp", "pinterest", "youtube"]):
            continue

        # Score de pertinence
        score = 0
        if any(w in t_low for w in ["officiel", "official", "postuler", "apply", "page officielle", "site officiel"]):
            score += 10
        if any(w in t_low for w in ["cliquez ici", "click here", "en savoir plus", "learn more"]):
            score += 3
        if any(dom in parsed.netloc for dom in [".edu", ".ac.", "univ", ".gouv", ".gov", ".org"]):
            score += 5
        if any(w in h_low for w in ["apply", "admission", "scholarship", "fellowship", "bourse"]):
            score += 4

        if score > 0:
            candidates.append((score, text, href, parsed.netloc))

    candidates.sort(key=lambda x: x[0], reverse=True)
    for score, text, href, netloc in candidates[:3]:
        print(f"  [Score {score}] '{text[:35]}' -> {href} ({netloc})")

for u in sample_urls:
    find_official_link(u)
