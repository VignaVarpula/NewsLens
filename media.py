from fastapi import FastAPI, Form, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from datetime import datetime, timedelta
from transformers import pipeline
import requests
import uvicorn
from gtts import gTTS
import os
import uuid
from fastapi.staticfiles import StaticFiles
from googletrans import Translator
from functools import lru_cache
import threading
from urllib.parse import urlparse

translator = Translator()
summarizer = pipeline("summarization", model="facebook/bart-large-cnn")

AUDIO_EXPIRY_HOURS = 1
MEDIASTACK_API_KEY = "d3d369b7b52153cb4fa6b62f20b704d8"
MEDIASTACK_API_URL = "http://api.mediastack.com/v1/news"

SUPPORTED_LANGUAGES = {
    "en": "English",
    "ar": "Arabic",
    "cn": "Chinese",
    "nl": "Dutch",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "jp": "Japanese",
    "kr": "Korean",
    "pt": "Portuguese",
    "ru": "Russian",
    "es": "Spanish",
    "hi": "Hindi"
}

SUPPORTED_CATEGORIES = {
    "": "All",
    "politics": "Politics",
    "sports": "Sports",
    "technology": "Technology",
    "business": "Business",
    "entertainment": "Entertainment",
    "health": "Health",
    "science": "Science"
}

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
app.add_middleware(CORSMiddleware, allow_origins=[""], allow_credentials=True, allow_methods=[""], allow_headers=["*"])

@app.get("/", response_class=HTMLResponse)
def form():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()

def generate_voice(summary_text, lang_code='en'):
    try:
        tts = gTTS(text=summary_text, lang=lang_code)
        filename = f"{uuid.uuid4()}.mp3"
        filepath = f"static/{filename}"
        tts.save(filepath)
        return f"/static/{filename}"
    except Exception:
        return None

def cleanup_old_audio():
    now = datetime.now()
    for file in os.listdir("static"):
        if file.endswith(".mp3"):
            file_path = os.path.join("static", file)
            file_time = datetime.fromtimestamp(os.path.getmtime(file_path))
            if now - file_time > timedelta(hours=AUDIO_EXPIRY_HOURS):
                try:
                    os.remove(file_path)
                except Exception:
                    pass

def schedule_cleanup():
    cleanup_old_audio()
    threading.Timer(3600, schedule_cleanup).start()

schedule_cleanup()

@lru_cache(maxsize=128)
def cached_api_call(query, from_date, to_date, language, category, limit, offset):
    params = {
        "access_key": MEDIASTACK_API_KEY,
        "keywords": query,
        "date": f"{from_date},{to_date}",
        "languages": language,
        "limit": limit,
        "offset": offset,
        "sort": "published_desc"
    }
    if category:
        params["categories"] = category
    res = requests.get(MEDIASTACK_API_URL, params=params)
    res.raise_for_status()
    return res.json()

def get_favicon_url(article_url):
    try:
        parsed_url = urlparse(article_url)
        domain = f"{parsed_url.scheme}://{parsed_url.netloc}"
        return f"https://www.google.com/s2/favicons?sz=64&domain={domain}"
    except Exception:
        return "https://via.placeholder.com/200x150?text=No+Image"

@app.post("/fetch_news", response_class=HTMLResponse)
def fetch_news(
    query: str = Form(...), 
    date: str = Form(...), 
    language: str = Form(...), 
    category: str = Form(""), 
    page: int = Form(1)
):
    try:
        start_date = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        return "<h3>Invalid date format. Please select a valid date.</h3>"

    today = datetime.today().date()
    if start_date > today:
        return "<h3>Future date is not allowed.</h3>"

    translated_query = query
    if language != "en":
        try:
            translated_query = translator.translate(query, src="en", dest=language).text
        except:
            translated_query = query

    limit = 20
    offset = (page - 1) * limit

    try:
        data = cached_api_call(translated_query, start_date.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d"), language, category, limit, offset)
    except Exception as e:
        return f"<h3>Error fetching news: {str(e)}</h3>"

    articles = data.get("data", [])
    total_results = data.get("pagination", {}).get("total", 0)
    total_pages = (total_results + limit - 1) // limit

    if not articles:
        return "<h3>No articles found for the given query and date range.</h3>"

    seen_urls = set()
    seen_titles = set()
    filtered_articles = []

    for article in articles:
        url = article.get("url", "").strip()
        title = article.get("title", "").strip()
        if url in seen_urls or title in seen_titles or not url:
            continue
        seen_urls.add(url)
        seen_titles.add(title)
        filtered_articles.append(article)

    if not filtered_articles:
        return "<h3>No unique articles found after filtering duplicates.</h3>"

    result_html = ""
    for article in filtered_articles:
        url = article.get("url", "#")
        title = article.get("title", "No Title")
        image_url = article.get("image") or get_favicon_url(url)
        description = article.get("description", "")

        try:
            if len(description.split()) > 40 and language == "en":
                summary = summarizer(description, max_length=60, min_length=20, do_sample=False)[0]['summary_text']
            else:
                summary = description or "No description available."
        except Exception:
            summary = description or "No summary available."

        audio_url = generate_voice(summary, lang_code=language if language in ["en", "hi", "ar", "fr", "de", "it", "pt", "es"] else "en")
        audio_download = f'<a href="{audio_url}" download style="margin-left:10px;color:#0077cc;">⬇ Download</a>' if audio_url else ""

        result_html += f"""
        <div style=\"display:flex;gap:20px;align-items:center;padding:15px;margin-bottom:20px;background:white;border-radius:10px;box-shadow:0 2px 6px rgba(0,0,0,0.1);\">
            <img src=\"{image_url}\" style=\"width:200px;height:150px;object-fit:cover;border-radius:8px;\">
            <div style=\"flex:1;text-align:left;\">
                <h3><a href=\"{url}\" target=\"_blank\" rel=\"noopener noreferrer\">{title}</a></h3>
                <p>{summary}</p>
                <audio controls src=\"{audio_url}\"></audio>{audio_download}
            </div>
        </div>
        """

    pagination_html = "<div style='text-align:center;margin-top:30px;'>"
    if page > 1:
        pagination_html += f'<form style="display:inline;" action="/fetch_news" method="post">' \
                           f'<input type="hidden" name="query" value="{query}">' \
                           f'<input type="hidden" name="date" value="{date}">' \
                           f'<input type="hidden" name="language" value="{language}">' \
                           f'<input type="hidden" name="category" value="{category}">' \
                           f'<input type="hidden" name="page" value="{page - 1}">' \
                           f'<button type="submit" style="margin-right:20px;">⬅ Previous</button></form>'
    pagination_html += f" Page {page} of {total_pages} "
    if page < total_pages:
        pagination_html += f'<form style="display:inline;" action="/fetch_news" method="post">' \
                           f'<input type="hidden" name="query" value="{query}">' \
                           f'<input type="hidden" name="date" value="{date}">' \
                           f'<input type="hidden" name="language" value="{language}">' \
                           f'<input type="hidden" name="category" value="{category}">' \
                           f'<input type="hidden" name="page" value="{page + 1}">' \
                           f'<button type="submit" style="margin-left:20px;">Next ➡</button></form>'
    pagination_html += "</div>"

    return f"""
    <html>
        <head><title>News Results</title></head>
        <body style="font-family:Arial;background:#f9f9f9;padding:20px;">
            <h2>Results for '{query}' from {start_date} to {today} ({SUPPORTED_LANGUAGES.get(language, language)}) - Category: {SUPPORTED_CATEGORIES.get(category, 'All')}</h2>
            {result_html}
            {pagination_html}
            <a href="/" style="display:block;margin-top:30px;font-weight:bold;color:#0077cc;">🔙 Search Again</a>
        </body>
    </html>
    """

if __name__== "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8002)