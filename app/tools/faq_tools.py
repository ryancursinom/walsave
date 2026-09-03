from langchain.tools import tool
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import FAISS
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from app.config.config import FAQ_PDF_PATH, GEMINI_API_KEY

loader = PyPDFLoader(FAQ_PDF_PATH)
docs = loader.load()

@tool
def faq_retriever(question: str) -> str:
    """Busca no FAQ oficial os trechos mais relevantes para entender a pergunta."""

    splitter = RecursiveCharacterTextSplitter(chunk_size=700,chunk_overlap=150)
    chunks = splitter.split_documents(docs)

    embedding = GoogleGenerativeAIEmbeddings(
        model="gemini-embedding-2-preview",
        google_api_key=GEMINI_API_KEY
    )

    latent_space = FAISS.from_documents(chunks,embedding)

    results = latent_space.similarity_search(question,k=6)
    return results