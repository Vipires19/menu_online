import requests
import os
import urllib
import urllib.parse
from pymongo import MongoClient
from pymongo.server_api import ServerApi

mongo_user = os.getenv('MONGO_USER')
mongo_pass = os.getenv("MONGO_PASS")

username = urllib.parse.quote_plus(mongo_user)
password = urllib.parse.quote_plus(mongo_pass)
client = MongoClient("mongodb+srv://%s:%s@cluster0.gjkin5a.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0" % (username, password), ssl = True)
db = client.teste
coll = db.usuarios

class Webhook():
    def __init__(self):
        super().__init__()

    def create_webhook(self,name, access_token):    
        url = "https://sandbox.asaas.com/api/v3/webhook"

        payload = { "name": name,
                    "sendType": "SEQUENTIALLY",
                    "url": "https://c023061ca738.ngrok-free.app/webhook",
                    "email": "viinycampos19@hotmail.com",
                    "enabled" : True,
                    "interrupted": False,
                    "apiVersion": 3,
                    "events": [
                                "PAYMENT_CREDIT_CARD_CAPTURE_REFUSED",
                                "PAYMENT_CHECKOUT_VIEWED",
                                "PAYMENT_BANK_SLIP_VIEWED",
                                "PAYMENT_DUNNING_REQUESTED",
                                "PAYMENT_DUNNING_RECEIVED",
                                "PAYMENT_AWAITING_CHARGEBACK_REVERSAL",
                                "PAYMENT_CHARGEBACK_DISPUTE",
                                "PAYMENT_CHARGEBACK_REQUESTED",
                                "PAYMENT_RECEIVED_IN_CASH_UNDONE",
                                "PAYMENT_REFUND_IN_PROGRESS",
                                "PAYMENT_REFUNDED",
                                "PAYMENT_RESTORED",
                                "PAYMENT_DELETED",
                                "PAYMENT_OVERDUE",
                                "PAYMENT_ANTICIPATED",
                                "PAYMENT_RECEIVED",
                                "PAYMENT_CONFIRMED",
                                "PAYMENT_UPDATED",
                                "PAYMENT_CREATED",
                                "PAYMENT_REPROVED_BY_RISK_ANALYSIS",
                                "PAYMENT_APPROVED_BY_RISK_ANALYSIS",
                                "PAYMENT_AWAITING_RISK_ANALYSIS",
                                "PAYMENT_AUTHORIZED"
                            ]
}
        
        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "access_token": access_token
        }

        response = requests.post(url, json=payload, headers=headers)

        print(response.text)

    def create_user(self,nome,id,cpf,email,celular,endereco,numero,bairro,cep):
        entregador = {"name": nome,
        "id": id,
        "cpfCnpj": cpf,
        "email": email,
        "mobilePhone": celular,
        "address": endereco,
        "addressNumber": numero,
        "province": bairro,
        "postalCode": cep,
        }

        entry = [entregador]
        
        return coll.insert_many(entry)

    def get_user_by_name(self, name):
        entregadores = coll.find({'name' : name})
        entregadores_df = []
        for entregador in entregadores:
            entregadores_df.append(entregador)
        
        user = entregadores_df[0]
        return user 
 