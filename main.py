from ollama import chat

class LLM:
    def ask(self, prompt):
        response = chat(
            model='llama3.2:3b',
            messages=[{'role': 'user', 'content': prompt}]
        )
        return response['message']['content']

if __name__ == '__main__':
    client = LLM()
    while True:
        message = input('your message: ')
        print(client.ask(message))