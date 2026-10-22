import logging
from typing import Dict, Any, List
from .base import BaseTool
from .web_search_tool import WebSearchTool
from ..security.log_redaction import summarize_exception, summarize_private_text

logger = logging.getLogger(__name__)

class CultureTool(BaseTool):
    def __init__(self, key_manager: Any):
        super().__init__(
            name="culture_tool",
            description="Returns curated recommendations for Movies, Books, Music, or Series with scores and details."
        )
        self.web_search = WebSearchTool(key_manager)

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["movie", "book", "music", "series"],
                    "description": "The category of recommendation needed."
                },
                "genre": {
                    "type": "string",
                    "description": "Specific genre (e.g., 'Sci-Fi', 'Rock', 'Dystopian'). Optional."
                },
                "mood": {
                    "type": "string",
                    "description": "User's current mood or desired vibe (e.g., 'Sad', 'Energetic'). Optional."
                },
                "specific_query": {
                    "type": "string",
                    "description": "Any specific request details (e.g., 'films like Inception'). Optional."
                }
            },
            "required": ["category"]
        }

    async def execute(self, category: str, genre: str = "", mood: str = "", specific_query: str = "") -> Dict[str, Any]:
        """
        Orchestrates the recommendation process:
        1. Formulates a search query to get high-rated/trending items.
        2. Uses WebSearchTool to get raw data (IMDb, Metacritic, Best-of lists).
        3. Returns a structured prompt context for the LLM to generate the final response.
        """
        try:
            # 1. Formulate Search Query
            search_query = ""
            if category == "movie":
                search_query = f"best {genre} movies 2024 2025 imdb metacritic scores" if genre else "best movies 2025 imdb metacritic"
            elif category == "series":
                search_query = f"best {genre} tv series 2024 2025 imdb ratings" if genre else "top trending tv shows 2025"
            elif category == "book":
                search_query = f"best selling {genre} books 2024 2025 goodreads" if genre else "best books 2024 2025 goodreads list"
            elif category == "music":
                 if "random" in specific_query.lower() or "rastgele" in specific_query.lower():
                     # For random music, we might not need search, just LLM creativity, or search for "hit songs of [random year]"
                     import random
                     year = random.randint(1970, 2024)
                     search_query = f"billboard top 100 songs of {year}"
                 else:
                    search_query = f"best {genre} songs albums 2024 2025 review" if genre else "top music hits 2025"

            if specific_query:
                search_query = specific_query + " reviews ratings"

            # 2. Perform Search
            logger.info("CultureTool search: %s", summarize_private_text(search_query, label="culture_query"))
            search_res_obj = await self.web_search.execute(search_query)
            search_results = search_res_obj.get("result", "")
            logger.info(f"📝 CultureTool search result length: {len(str(search_results))}")
            
            if not search_results and "error" in search_res_obj:
                search_results = f"Arama hatası: {search_res_obj['error']}"
                logger.error("CultureTool search provider returned an error")

            # 3. Construct Context for LLM
            # We don't generate the final text here; we return the data for the ChatEngine to roleplay the response.
            context_text = (
                f"--- CULTURE TOOL DATA ---\n"
                f"Category: {category}\n"
                f"User Req: {genre} {mood} {specific_query}\n"
                f"Search Context (Use these for factual scores/titles):\n{str(search_results)[:2000]}\n"
                f"--- INSTRUCTIONS ---\n"
                f"Using the data above, recommend 3-5 distinct items.\n"
                f"For each item, include:\n"
                f"- Title (Original & TR if available)\n"
                f"- IMDb/Metacritic/Goodreads Score (Find in text or estimate based on 'acclaimed')\n"
                f"- Brief, engaging description explaining WHY it fits the user's mood '{mood}'.\n"
                f"- If Music: Suggest a specific Track + Artist + Album.\n"
                f"Style: Sophisticated cultural sourmelier but friendly."
            )
            return {"result": context_text}

        except Exception as e:
            logger.error("CultureTool error: %s", summarize_exception(e, label="culture"))
            return {"error": "culture_provider_failed"}
