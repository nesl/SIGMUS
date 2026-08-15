import os
from openai import OpenAI
import base64
from utilities.util import get_config

class ImageCaptioner:

    def __init__(self):

        # Get config data
        self.config = get_config()
        self.vlm_data = self.config["vlm_host"]

        self.client = OpenAI(
            base_url=self.vlm_data["uri"],
            api_key="no-key-needed",
        )
        self.model = "NVILA-15B"
    
    # Converts an image file to a base64 encoding
    def file_to_base64_binary(self, file_path):
        # assert file_path.lower().endswith(".mp4", ".jpg", ".jpeg", ".png", )

        # Determine type
        file_ext = file_path.split(".")[-1]
        

        with open(file_path, "rb") as file:
            b64_image = base64.b64encode(file.read()).decode("utf-8")
            b64_image_text = "data:image/"+file_ext+";base64," + b64_image
        
        return b64_image_text
            
    
    def image_caption(self, image_path, prompt="What's in this image, and is there anything interesting?"):
        """
        Generate a caption for the image using the docker VLM
        """

        caption_path = image_path[:-3] + "caption"
        caption = ""
        if os.path.exists(caption_path):
            with open(caption_path, "r") as f:
                caption = f.read()
                return caption

        # Load the image file
        with open(image_path, "rb") as image_file:
            image_data = self.file_to_base64_binary(image_path)

        
        response = self.client.chat.completions.create(
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                # "url": "https://blog.logomyway.com/wp-content/uploads/2022/01/NVIDIA-logo.jpg",
                                # Or you can pass in a base64 encoded image
                                "url": image_data,
                            },
                        },
                    ],
                }
            ],
            model=self.model,
        )

        caption = response.choices[0].message.content

        # Save the caption to the image path
        with open(caption_path, "w") as f:
            f.write(caption)
    
        return caption
