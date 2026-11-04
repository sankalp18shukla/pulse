"""News sources for each category.

Pulse uses two kinds of source:
  1. GNews API (https://gnews.io) - used when GNEWS_API_KEY is set in .env. Free tier: 100 requests/day.
  2. RSS feeds from major publishers - always used, no key needed.

Edit the lists below to add or remove publishers. A feed that fails is skipped silently.
"""

CATEGORIES = {
    "top": {
        "label": "Top stories",
        "gnews": {"category": "general", "country": "in"},
        "rss": [
            "https://feeds.bbci.co.uk/news/rss.xml",
            "https://www.thehindu.com/news/feeder/default.rss",
            "https://timesofindia.indiatimes.com/rssfeedstopstories.cms",
        ],
    },
    "india": {
        "label": "India",
        "gnews": {"category": "nation", "country": "in"},
        "rss": [
            "https://www.thehindu.com/news/national/feeder/default.rss",
            "https://feeds.bbci.co.uk/news/world/asia/india/rss.xml",
            "https://indianexpress.com/section/india/feed/",
        ],
    },
    "world": {
        "label": "World",
        "gnews": {"category": "world"},
        "rss": [
            "https://feeds.bbci.co.uk/news/world/rss.xml",
            "https://www.aljazeera.com/xml/rss/all.xml",
            "https://www.thehindu.com/news/international/feeder/default.rss",
        ],
    },
    "business": {
        "label": "Business",
        "gnews": {"category": "business", "country": "in"},
        "rss": [
            "https://feeds.bbci.co.uk/news/business/rss.xml",
            "https://economictimes.indiatimes.com/rssfeedstopstories.cms",
            "https://www.thehindu.com/business/feeder/default.rss",
        ],
    },
    "tech": {
        "label": "Tech",
        "gnews": {"category": "technology"},
        "rss": [
            "https://feeds.bbci.co.uk/news/technology/rss.xml",
            "https://techcrunch.com/feed/",
            "https://www.thehindu.com/sci-tech/technology/feeder/default.rss",
        ],
    },
    "science": {
        "label": "Science",
        "gnews": {"category": "science"},
        "rss": [
            "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
            "https://www.thehindu.com/sci-tech/science/feeder/default.rss",
        ],
    },
    "sports": {
        "label": "Sports",
        "gnews": {"category": "sports", "country": "in"},
        "rss": [
            "https://feeds.bbci.co.uk/sport/rss.xml",
            "https://www.espncricinfo.com/rss/content/story/feeds/0.xml",
            "https://www.thehindu.com/sport/feeder/default.rss",
        ],
    },
    "health": {
        "label": "Health",
        "gnews": {"category": "health"},
        "rss": [
            "https://feeds.bbci.co.uk/news/health/rss.xml",
            "https://www.thehindu.com/sci-tech/health/feeder/default.rss",
        ],
    },
}
