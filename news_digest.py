import feedparser
from datetime import datetime, timedelta
import pytz
import asyncio
import aiohttp
import certifi
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import logging
from typing import List, Dict, Any
import cachetools
import aiofiles
import ssl
import os
import json
import base64
from github import Github
import time

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Simple memory cache (10 minute TTL)
feed_cache = cachetools.TTLCache(maxsize=100, ttl=600)

def parse_feed_content(content: str, cutoff_time: datetime) -> List[Dict[str, Any]]:
    """Parse feed content in a separate thread"""
    if not content:
        return []
    
    try:
        feed = feedparser.parse(content)
        recent_posts = []
        
        for entry in feed.entries:
            published_time = None
            for time_field in ['published_parsed', 'updated_parsed']:
                if hasattr(entry, time_field):
                    published_time = time.struct_time(getattr(entry, time_field))
                    break
            
            if published_time:
                published_datetime = datetime.fromtimestamp(time.mktime(published_time))
                published_datetime = pytz.UTC.localize(published_datetime)
                
                if published_datetime > cutoff_time:
                    recent_posts.append({
                        'title': entry.title,
                        'link': entry.link,
                        'published': published_datetime.isoformat(),
                        'source': feed.feed.title if hasattr(feed.feed, 'title') else "Unknown Source"
                    })
        
        return recent_posts
    except Exception as e:
        logger.error(f"Error parsing feed: {str(e)}")
        return []

async def fetch_feed_content(session: aiohttp.ClientSession, url: str, timeout: int = 10) -> str:
    """Asynchronously fetch feed content with timeout"""
    try:
        async with session.get(url, timeout=timeout) as response:
            if response.status == 200:
                return await response.text()
    except Exception as e:
        logger.error(f"Error fetching {url}: {str(e)}")
    return ""

async def process_feed(url: str, session: aiohttp.ClientSession, 
                      cutoff_time: datetime, executor: ThreadPoolExecutor) -> List[Dict[str, Any]]:
    """Process a single feed with caching"""
    try:
        if url in feed_cache:
            return feed_cache[url]
        
        content = await fetch_feed_content(session, url)
        if not content:
            return []
        
        loop = asyncio.get_running_loop()
        posts = await loop.run_in_executor(
            executor,
            partial(parse_feed_content, content, cutoff_time)
        )
        
        if posts:
            feed_cache[url] = posts
        
        return posts
    except Exception as e:
        logger.error(f"Error processing {url}: {str(e)}")
        return []

def create_html_content(posts: List[Dict[str, Any]]) -> str:
    """Create HTML content with improved typography and mobile responsiveness"""
    current_date = datetime.now().strftime('%B %d, %Y')
    
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Daily Gazette - {current_date}</title>
        <style>
            body {{ 
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen-Sans, Ubuntu, Cantarell, sans-serif;
                max-width: 800px; 
                margin: 0 auto; 
                padding: 20px;
                font-size: 16px;
                line-height: 1.6;
                color: #333;
                background-color: #f5f5f5;
            }}
            .container {{
                background: white;
                padding: 30px;
                border-radius: 10px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            }}
            h1 {{ 
                color: #1a1a1a; 
                font-size: 32px;
                margin-bottom: 10px;
                display: flex;
                align-items: center;
            }}
            .tagline {{
                color: #666;
                font-size: 18px;
                margin-bottom: 30px;
            }}
            .source {{ 
                color: #2c3e50;
                font-size: 24px;
                font-weight: bold;
                margin: 30px 0 20px 0;
                padding-bottom: 5px;
                border-bottom: 2px solid #eee;
            }}
            a {{ 
                color: #2980b9; 
                text-decoration: none;
                font-size: 18px;
                transition: color 0.2s;
            }}
            a:hover {{ 
                color: #3498db;
                text-decoration: underline; 
            }}
            .post {{ 
                margin-bottom: 20px;
                padding: 10px 0;
            }}
            .post:hover {{
                background-color: #f8f9fa;
            }}
            .logo {{
                font-size: 40px;
                margin-right: 10px;
            }}
            .timestamp {{
                color: #666;
                font-size: 14px;
                margin-top: 5px;
            }}
            @media (max-width: 600px) {{
                body {{
                    padding: 10px;
                }}
                .container {{
                    padding: 15px;
                }}
                h1 {{
                    font-size: 24px;
                }}
                .logo {{
                    font-size: 30px;
                }}
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1><span class="logo">🗞️</span>Daily Gazette</h1>
            <div class="tagline">News digest for {current_date}</div>
    """
    
    if not posts:
        html += "<p>No new posts in the last 24 hours.</p>"
    else:
        # Sort posts by published date
        sorted_posts = sorted(posts, key=lambda x: x['published'], reverse=True)
        
        # Group posts by source
        posts_by_source = {}
        for post in sorted_posts:
            source = post['source']
            if source not in posts_by_source:
                posts_by_source[source] = []
            posts_by_source[source].append(post)
        
        # Add posts grouped by source
        for source in sorted(posts_by_source.keys()):
            html += f'<h2 class="source">{source}</h2>'
            for post in posts_by_source[source]:
                published_str = datetime.fromisoformat(post['published']).strftime('%I:%M %p')
                html += f"""
                <div class="post">
                    <a href="{post['link']}">{post['title']}</a>
                    <div class="timestamp">{published_str}</div>
                </div>
                """
    
    html += """
        </div>
    </body>
    </html>
    """
    return html

async def main():
    """Main async function"""
    # Configuration - these will come from environment variables in GitHub Actions
    FEEDS_FILE = os.getenv('FEEDS_FILE', 'rss_feeds.txt')
    GITHUB_TOKEN = os.environ['GITHUB_TOKEN']
    GITHUB_REPO = os.environ['GITHUB_REPOSITORY']  # format: "username/repo"
    
    # Initialize GitHub client
    g = Github(GITHUB_TOKEN)
    repo = g.get_repo(GITHUB_REPO)
    
    # Read feeds
    async with aiofiles.open(FEEDS_FILE, mode='r') as file:
        content = await file.read()
        feed_urls = [line.strip() for line in content.splitlines() if line.strip()]
    
    # Make sure cutoff_time is UTC aware
    cutoff_time = datetime.now(pytz.UTC) - timedelta(days=1)
    
    start_time = time.time()
    logger.info("Starting feed fetching...")
    
    # Process feeds concurrently
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    connector = aiohttp.TCPConnector(ssl=ssl_context)
    
    async with aiohttp.ClientSession(connector=connector) as session:
        with ThreadPoolExecutor(max_workers=min(8, len(feed_urls))) as executor:
            tasks = [
                process_feed(url, session, cutoff_time, executor)
                for url in feed_urls
            ]
            results = await asyncio.gather(*tasks)
    
    # Combine all posts
    all_posts = [post for posts in results for post in posts]
    
    logger.info(f"Fetched {len(all_posts)} posts in {time.time() - start_time:.2f} seconds")
    
    # Create HTML content
    html_content = create_html_content(all_posts)
    
    try:
        # Update or create index.html
        try:
            # Try to get the existing file
            contents = repo.get_contents("index.html")
            repo.update_file(
                contents.path,
                f"Update news digest - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                html_content,
                contents.sha
            )
        except Exception:
            # File doesn't exist, create it
            repo.create_file(
                "index.html",
                f"Create news digest - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                html_content
            )
        
        logger.info("Website updated successfully!")
    except Exception as e:
        logger.error(f"Error updating GitHub repository: {str(e)}")
        raise

if __name__ == "__main__":
    asyncio.run(main())
