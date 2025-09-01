import requests

class Waha:

    def __init__(self):
        self.__api_url = 'http://waha:3000'
    
    def verify_wid(self, phone_number,session):
        # Debug: imprime o número formatado
        print(f"DEBUG - Número recebido: '{phone_number}'")

        # Certifique-se de que o número é uma string e remova espaços extras
        phone_number = str(phone_number).strip()

        # Nova URL correta para verificar se o número existe
        url = f"{self.__api_url}/api/contacts/check-exists?phone={phone_number}&session={session}"

        # Fazendo a requisição GET
        response = requests.get(url)

        # Depuração: Verifique a resposta da API
        print(f"DEBUG - Resposta da API: {response.text}")

        # Verifica se a resposta foi bem-sucedida
        if response.status_code == 200:
            data = response.json()
            if data.get("numberExists"):  # Se o número existe
                chat_id = data.get("chatId")
                print(f"✅ Chat ID encontrado: {chat_id}")
                return chat_id
            else:
                print("❌ Erro: Número não registrado no WhatsApp.")
        else:
            print(f"⚠️ Erro na requisição: {response.status_code} - {response.text}")

        return None  # Retorna None caso não encontre o número

    def send_message(self, chat_id, message,session):
        url = f'{self.__api_url}/api/sendText'
        headers = {
            'Content-Type': 'application/json',
        }
        payload = {
            'session': session,
            'chatId': chat_id,
            'text': message,
        }
        requests.post(
            url=url,
            json=payload,
            headers=headers,
        )

    def start_typing(self, chat_id,session):
        url = f'{self.__api_url}/api/startTyping'
        headers = {
            'Content-Type': 'application/json',
        }
        payload = {
            'session': session,
            'chatId': chat_id,
        }
        requests.post(
            url=url,
            json=payload,
            headers=headers,
        )

    def stop_typing(self, chat_id,session):
        url = f'{self.__api_url}/api/stopTyping'
        headers = {
            'Content-Type': 'application/json',
        }
        payload = {
            'session': session,
            'chatId': chat_id,
        }
        requests.post(
            url=url,
            json=payload,
            headers=headers,
        )