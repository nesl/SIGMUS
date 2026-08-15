import requests
from utilities.util import *
from openai import OpenAI

#bot_response = response['message']['content']
 
    # # Obtain the <think> and response
    # bot_thoughts = ""
    # bot_output = bot_response
    # print("Produced response...")
    # if "<think>" in bot_response:
    #     bot_thoughts = bot_response.split("<think>")[1].split("</think>")[0]
    #     bot_output = bot_response.split("</think>")[1].strip()
    
    # print("Returning response")


class OpenAIClient:

    def __init__(self):
        
        self.config = get_config()
        self.openai = self.config["openai"]
        api_key = self.openai["api"]

        # Set up client
        self.client = OpenAI(api_key=api_key)

        # Additional stuff
        self.model = "gpt-4o"
        self.message_history = []        

    
    # Does not use memory
    def send_message_to_llm_single(self, user_input, temperature=0.0):

        user_message = {"role": "user", "content": user_input, "temperature":temperature}

       # Send to llm
        llm_response = self.client.chat.completions.create(
            model=self.model,
            messages=[user_message]
        )

        llm_response = llm_response.choices[0].message.content

        return llm_response, None

    def send_message_to_llm_historic(self, user_input, temperature=0.0):

        user_input = "\nUSER Request: \n" + user_input
        user_message = {"role": "user", "content": user_input, "temperature":temperature}
        self.message_history.append(user_message)

        # Send to llm
        llm_response = self.client.chat.completions.create(
            model=self.model,
            messages=self.message_history
        )

        llm_response = "LLM Response: \n" + llm_response.choices[0].message.content
        llm_message = {"role": "assistant", "content": llm_response}
        self.message_history.append(llm_message)

        return llm_response


class LLMClient:

    def __init__(self, url):
        self.url = url
        self.message_history = []

    # Parse responses
    def buffer_response(self, response):

        # Streaming get to avoid LLM full text decode latency
        #  Latency should only depend on network and LLM prefill latency
        chunk_buffer = []
        for chunk in response.iter_lines(decode_unicode=True):
            if len(chunk) == 0:
                chunk = "\n" # Swap to new lines
            chunk_buffer.append(chunk)
        
        response_data = "\n".join(chunk_buffer)
        

        # Obtain the <think> and response (if used)
        bot_thoughts = ""
        bot_output = response_data
        if "<think>" in response_data:
            bot_thoughts = response_data.split("<think>")[1].split("</think>")[0]
            bot_output = response_data.split("</think>")[1].strip()
        
        return bot_output, bot_thoughts
    
    def clear_history(self):
        self.message_history = []

    def send_message_to_llm_historic(self, user_input, temperature=0.0):

        user_input = "\nUSER Request: \n" + user_input
        user_message = {"role": "user", "content": user_input, "temperature":temperature}
        self.message_history.append(user_message)

        # Make the POST request to the Flask server
        response = requests.post(self.url, json=self.message_history, stream = True)

        # Check if the request was successful
        if response.status_code == 200:
            # Parse the response from Flask
            llm_response, llm_thoughts = self.buffer_response(response)

            llm_response = "LLM Response: \n" + llm_response
            # Now, add the response to the message history
            bot_message = {"role": "assistant", "content": llm_response}
            self.message_history.append(bot_message)

            return llm_response, llm_thoughts
        else:
            print(f"Failed to get response from Flask server. Status code: {response.status_code}")
            return None
        
    def send_message_to_llm_pair(self, user_input):

        user_message = {"role": "user", "content": user_input}

        self.message_history.append(user_message)

        # Make the POST request to the Flask server
        response = requests.post(self.url, json=[user_message], stream = True)

        # Check if the request was successful
        if response.status_code == 200:
            # Parse the response from Flask
            llm_response, llm_thoughts = self.buffer_response(response)

            # Now, add the response to the message history
            bot_message = {"role": "assistant", "content": llm_response}
            self.message_history.append(bot_message)

            # Limit the message history
            if len(self.message_history) > 2:
                self.message_history = self.message_history[:2] + self.message_history[-2:]
            else:
                self.message_history = self.message_history[:2]

            return llm_response, llm_thoughts
        else:
            print(f"Failed to get response from Flask server. Status code: {response.status_code}")
            return None

    # Does not use memory
    def send_message_to_llm_single(self, user_input, temperature=0.0):

        user_message = {"role": "user", "content": user_input, "temperature":temperature}

        # Make the POST request to the Flask server
        response = requests.post(self.url, json=[user_message], stream = True)

        # Check if the request was successful
        if response.status_code == 200:
            # Parse the response from Flask
            llm_response, llm_thoughts = self.buffer_response(response)

            return llm_response, llm_thoughts
        else:
            print(f"Failed to get response from Flask server. Status code: {response.status_code}")
            return None, None





if __name__ == "__main__":

    #message_history = []

    test = OpenAIClient()

    # Create the LLM client
    # llm_client = LLMClient("https://fgagpo3z06n7.share.zrok.io/api")

    # user_message = "how many times did I ask you about how you are today?"
    # response, thoughts = llm_client.send_message_to_llm_single(user_message)
    # print(response)

    # user_input = ""
    # while True:

    #     user_input = input("Type message here (q to quit): ")

    #     if user_input == "q":
    #         break

    #     user_message = {"role": "user", "content": user_input}

    #     # Update our message history
    #     message_history.append(user_message)

        # Get LLM response
        # bot_response = send_messages_to_flask(message_history)

        # if bot_response:
        #     bot_input, bot_thoughts = bot_response  # Bot thoughts are the <think>...</think> fields from deepseek
        #     print(bot_input)

        #     # Append to our history
        #     bot_message = {"role": "assistant", "content": bot_input}
            
        #     # Update our message history
        #     message_history.append(bot_message)