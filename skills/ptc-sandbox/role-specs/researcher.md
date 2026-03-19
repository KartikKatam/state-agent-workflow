## Your PTC Packages (Researcher)

Extraction: trafilatura, html2text, markdownify, readability-lxml
Documents: pypdf, pdfplumber, markdown-it-py
Matching: rapidfuzz
Data: pandas
HTTP: requests
Tokens: tiktoken

**Pre-installed in `ptc-researcher:latest` image.** No pip install delay.

## When to Use PTC (Researcher)

- **Fetch and clean web content:** requests fetches HTML, trafilatura strips
  nav/ads/boilerplate. Process in container, print clean text summary.
- **Deduplicate sources:** rapidfuzz detects >80% similar content across sources.
  Corroborated sources get higher confidence scores.
- **Parse PDFs:** pypdf/pdfplumber extract text from papers/specs without full
  document entering context.
- **Compare libraries:** pandas pivot tables for feature comparison across sources.
- **Bulk API queries:** requests + loops for paginated API data collection.

### Recipe: Fetch and Clean Web Content

```python
import requests, json
from trafilatura import extract

resp = requests.get("https://docs.example.com/api/reference", timeout=15)
clean = extract(resp.text)
# Summarize to keep context small
print(json.dumps({
    "url": resp.url,
    "clean_text": clean[:500] if clean else "",
    "length": len(clean or ""),
    "status": resp.status_code,
}))
```

### Recipe: Source Deduplication

```python
import json
from rapidfuzz import fuzz

sources = [...]  # list of (title, text) tuples
unique = [sources[0]]
for title, text in sources[1:]:
    if all(fuzz.ratio(text, u[1]) < 80 for u in unique):
        unique.append((title, text))
print(json.dumps({"total": len(sources), "unique": len(unique), "duplicates_removed": len(sources) - len(unique)}))
```

### Recipe: PDF Extraction

```python
import json
from pypdf import PdfReader

reader = PdfReader("/workspace/docs/spec.pdf")
text = ""
for page in reader.pages[:10]:  # limit pages to avoid timeout
    text += page.extract_text() + "\n"
# Print summary, not full text
print(json.dumps({
    "pages": len(reader.pages),
    "extracted_chars": len(text),
    "preview": text[:500],
}))
```

### Recipe: Multi-URL Comparison

```python
import requests, json
from trafilatura import extract

urls = [
    "https://lib-a.readthedocs.io/en/latest/",
    "https://lib-b.readthedocs.io/en/latest/",
]
comparison = {}
for url in urls:
    try:
        resp = requests.get(url, timeout=10)
        clean = extract(resp.text) or ""
        comparison[url] = {"status": resp.status_code, "content_length": len(clean)}
    except Exception as e:
        comparison[url] = {"error": str(e)}
print(json.dumps(comparison))
```

## When NOT to Use PTC (Researcher)

- The LLM is already great at NLP. Don't install spaCy/NLTK -- you handle language natively.
- Simple MCP queries -- call Context7/WebSearch directly, process results in context.
