
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <html>
        <head>
            <meta charset="UTF-8">
            <title>My SNS</title>
        </head>
        <body>
            <h1>ようこそ！My SNSへ！</h1>
            <p>初めてのSNSアプリ開発に成功！</p>
        </body>
    </html>
    """

