import aiohttp
import json


class LabelStudioAPI:
    """
    A class to interact with the Label Studio API.

    Args:
        base_url (str): The base URL for the Label Studio API.
        auth_token (str): The authentication token for the Label Studio API.
    """

    def __init__(self, base_url, auth_token):
        self.base_url = base_url
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Token {auth_token}",
        }

    async def import_task(self, project_id, data):
        """
        Import a task into a project.

        Args:
            project_id (int): The ID of the project.
            data (dict): The task data to import.

        Returns:
            dict: The response from the API.

        Raises:
            HTTPError: If the request to the Label Studio API fails.
        """
        url = f"{self.base_url}/api/projects/{project_id}/import"
        return await self.make_post_request(url, data)

    async def make_post_request(self, url, data):
        """
        Make a POST request to the Label Studio API.

        Args:
            url (str): The URL to make the request to.
            data (dict): The data to send in the request.

        Returns:
            dict: The response from the API.

        Raises:
            HTTPError: If the response status code is not OK.
        """
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, headers=self.headers, data=json.dumps(data)
            ) as response:
                response.raise_for_status()
                return await response.json()
