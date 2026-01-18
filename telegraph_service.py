import httpx
import json
import os
from tenacity import retry, stop_after_attempt, wait_exponential

class TelegraphService:
    BASE_URL = "https://api.telegra.ph"

    def __init__(self, short_name="McdBot", author_name="McDonalds Bot"):
        self.short_name = short_name
        self.author_name = author_name
        self.access_token = None
        # Try to load token from environment or file if we wanted persistence, 
        # but for now we can create a new one or keep it in memory.
        # Ideally, we should cache this token.
        token_dir = os.path.join("data")
        if not os.path.exists(token_dir):
            try:
                os.makedirs(token_dir)
            except Exception as e:
                print(f"Error creating telegraph token directory: {e}")
                token_dir = "."
        self.token_file = os.path.join(token_dir, "telegraph_token.json")
        self._load_token()

    def _load_token(self):
        if os.path.exists(self.token_file):
            try:
                with open(self.token_file, "r") as f:
                    data = json.load(f)
                    self.access_token = data.get("access_token")
            except Exception as e:
                print(f"Error loading telegraph token: {e}")

    def _save_token(self, token):
        self.access_token = token
        try:
            with open(self.token_file, "w") as f:
                json.dump({"access_token": token}, f)
        except Exception as e:
            print(f"Error saving telegraph token: {e}")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
    async def create_account(self):
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.BASE_URL}/createAccount",
                params={
                    "short_name": self.short_name,
                    "author_name": self.author_name
                }
            )
            data = response.json()
            if data.get("ok"):
                self._save_token(data["result"]["access_token"])
                return self.access_token
            raise Exception(f"Failed to create Telegraph account: {data}")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
    async def create_page(self, title, content_nodes):
        """
        content_nodes: List of Node objects.
        Node: String or {'tag': 'p', 'children': ['Hello']}
        """
        if not self.access_token:
            await self.create_account()
            if not self.access_token:
                return None

        async with httpx.AsyncClient(timeout=30.0) as client:
            content_json = json.dumps(content_nodes)
            response = await client.post(
                f"{self.BASE_URL}/createPage",
                data={
                    "access_token": self.access_token,
                    "title": title,
                    "content": content_json,
                    "return_content": False
                }
            )
            data = response.json()
            if data.get("ok"):
                return data["result"]["url"]
            raise Exception(f"Failed to create Telegraph page: {data}")

    @staticmethod
    def format_calendar_to_nodes(calendar_data):
        """
        Converts calendar data (list of dicts) to Telegraph Nodes.
        Expected data structure:
        [
            {
                "title": "Campaign Title",
                "start": "2024-01-01",
                "end": "2024-01-02",
                "content": "Description",
                "image": "http://..."
            },
            ...
        ]
        """
        nodes = []
        
        # Intro
        nodes.append({"tag": "p", "children": ["麦当劳近期活动一览："]})
        nodes.append({"tag": "hr"})

        for item in calendar_data:
            # Item Title
            title = item.get("title", "未知活动")
            nodes.append({"tag": "h4", "children": [title]})
            
            # Image
            image_url = item.get("image") or item.get("imageUrl") or item.get("img")
            if image_url:
                nodes.append({"tag": "figure", "children": [
                    {"tag": "img", "attrs": {"src": image_url}}
                ]})

            # Date
            start = item.get("start", "")
            end = item.get("end", "")
            if start or end:
                date_str = f"时间: {start} - {end}"
                nodes.append({"tag": "p", "children": [{"tag": "b", "children": [date_str]}]})

            content = item.get("content") or item.get("desc")
            if content:
                for line in str(content).splitlines():
                    l = line.strip()
                    if not l:
                        continue
                    nodes.append({"tag": "p", "children": [l]})
            
            nodes.append({"tag": "hr"})

        nodes.append({"tag": "p", "children": ["Generated by McdBot"]})
        return nodes
