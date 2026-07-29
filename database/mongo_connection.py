from pymongo import MongoClient
import os

_mongo = MongoClient(os.getenv("MONGODB_URI", "mongodb://localhost:27017"))
db = _mongo["assessor"]
col_sessoes = db["sessoes"]

col_sessoes.create_index("session_id")
col_sessoes.create_index("iniciada_em")