import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from database import engine, SessionLocal
from models import Base, Post, User, Like, Comment, Follow

BASE_DIR = Path(__file__).resolve().parent
# 既存DBの投稿を消さずに、所有者IDの列だけ追加する。
with engine.begin() as conn:
    if 'posts' in [r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))]:
        cols = [r[1] for r in conn.execute(text('PRAGMA table_info(posts)'))]
        if 'author_id' not in cols:
            conn.execute(text('ALTER TABLE posts ADD COLUMN author_id INTEGER'))
Base.metadata.create_all(bind=engine)
app = FastAPI(title='My SNS')
ph = PasswordHasher()
security = HTTPBearer(auto_error=False)
# 初回起動時にローカル用の秘密鍵を生成して保持する（Gitに含めない）。
key_file = BASE_DIR / '.jwt_secret'
if os.environ.get('JWT_SECRET_KEY'):
    SECRET_KEY = os.environ['JWT_SECRET_KEY']
elif key_file.exists():
    SECRET_KEY = key_file.read_text(encoding='utf-8').strip()
else:
    SECRET_KEY = secrets.token_hex(32)
    key_file.write_text(SECRET_KEY, encoding='utf-8')

class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=40)
    password: str = Field(min_length=8, max_length=128)
class Content(BaseModel):
    content: str = Field(min_length=1, max_length=1000)

def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def current_user(credentials: HTTPAuthorizationCredentials = Depends(security), db=Depends(db_session)):
    if credentials is None or credentials.scheme.lower() != 'bearer':
        raise HTTPException(401, 'ログインしてください')
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=['HS256'])
        username = payload.get('sub')
        if not isinstance(username, str):
            raise ValueError()
    except (jwt.InvalidTokenError, ValueError):
        raise HTTPException(401, 'ログインの有効期限が切れました')
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise HTTPException(401, 'ユーザーが見つかりません')
    return user

def post_or_404(db, post_id):
    post = db.get(Post, post_id)
    if not post:
        raise HTTPException(404, '投稿が見つかりません')
    return post

def serialize_post(db, post, viewer_id=None):
    likes = db.query(Like).filter(Like.post_id == post.id).all()
    comments = db.query(Comment).filter(Comment.post_id == post.id).order_by(Comment.id).all()
    users = {u.id: u.username for u in db.query(User).all()}
    return {'id': post.id, 'username': post.username, 'content': post.content,
            'author_id': post.author_id, 'mine': post.author_id is not None and post.author_id == viewer_id,
            'likes': len(likes), 'liked': any(x.user_id == viewer_id for x in likes),
            'comments': [{'id': c.id, 'username': users.get(c.user_id, '退会ユーザー'), 'content': c.content} for c in comments]}

@app.get('/', response_class=HTMLResponse)
def home():
    return (BASE_DIR / 'templates' / 'index.html').read_text(encoding='utf-8')

@app.post('/register')
def register(data: Credentials, db=Depends(db_session)):
    username = data.username.strip()
    if not username:
        raise HTTPException(400, 'ユーザー名を入力してください')
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(400, 'そのユーザー名は使用されています')
    db.add(User(username=username, password_hash=ph.hash(data.password)))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, 'そのユーザー名は使用されています')
    return {'message': '登録しました'}

@app.post('/login')
def login(data: Credentials, db=Depends(db_session)):
    user = db.query(User).filter(User.username == data.username.strip()).first()
    if not user:
        raise HTTPException(401, 'ユーザー名かパスワードが違います')
    try:
        ph.verify(user.password_hash, data.password)
    except (VerifyMismatchError, VerificationError):
        raise HTTPException(401, 'ユーザー名かパスワードが違います')
    token = jwt.encode({'sub': user.username, 'exp': datetime.now(timezone.utc) + timedelta(hours=24)}, SECRET_KEY, algorithm='HS256')
    return {'access_token': token, 'token_type': 'bearer', 'username': user.username}

@app.get('/me')
def me(user=Depends(current_user)):
    return {'id': user.id, 'username': user.username}

@app.get('/posts')
def posts(db=Depends(db_session)):
    return [serialize_post(db, p) for p in db.query(Post).order_by(Post.id.desc()).limit(100).all()]

@app.get('/feed')
def feed(user=Depends(current_user), db=Depends(db_session)):
    ids = [f.following_id for f in db.query(Follow).filter(Follow.follower_id == user.id).all()] + [user.id]
    return [serialize_post(db, p, user.id) for p in db.query(Post).filter(Post.author_id.in_(ids)).order_by(Post.id.desc()).limit(100).all()]

@app.post('/posts')
def create_post(data: Content, user=Depends(current_user), db=Depends(db_session)):
    content = data.content.strip()
    if not content:
        raise HTTPException(400, '投稿内容を入力してください')
    post = Post(username=user.username, author_id=user.id, content=content)
    db.add(post); db.commit(); db.refresh(post)
    return serialize_post(db, post, user.id)

@app.put('/posts/{post_id}')
def edit_post(post_id: int, data: Content, user=Depends(current_user), db=Depends(db_session)):
    post = post_or_404(db, post_id)
    if post.author_id != user.id:
        raise HTTPException(403, '自分の投稿だけ編集できます')
    if not data.content.strip():
        raise HTTPException(400, '投稿内容を入力してください')
    post.content = data.content.strip(); db.commit()
    return {'message': '編集しました'}

@app.delete('/posts/{post_id}')
def delete_post(post_id: int, user=Depends(current_user), db=Depends(db_session)):
    post = post_or_404(db, post_id)
    if post.author_id != user.id:
        raise HTTPException(403, '自分の投稿だけ削除できます')
    db.query(Like).filter(Like.post_id == post_id).delete()
    db.query(Comment).filter(Comment.post_id == post_id).delete()
    db.delete(post); db.commit()
    return {'message': '削除しました'}

@app.post('/posts/{post_id}/like')
def toggle_like(post_id: int, user=Depends(current_user), db=Depends(db_session)):
    post_or_404(db, post_id)
    like = db.query(Like).filter(Like.post_id == post_id, Like.user_id == user.id).first()
    if like:
        db.delete(like)
    else:
        db.add(Like(post_id=post_id, user_id=user.id))
    db.commit()
    return {'liked': not bool(like)}

@app.post('/posts/{post_id}/comments')
def add_comment(post_id: int, data: Content, user=Depends(current_user), db=Depends(db_session)):
    post_or_404(db, post_id)
    if not data.content.strip():
        raise HTTPException(400, 'コメントを入力してください')
    db.add(Comment(post_id=post_id, user_id=user.id, content=data.content.strip())); db.commit()
    return {'message': 'コメントしました'}

@app.get('/users')
def users(db=Depends(db_session)):
    return [{'id': u.id, 'username': u.username} for u in db.query(User).order_by(User.username).all()]

@app.get('/users/{username}')
def profile(username: str, db=Depends(db_session)):
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(404, 'ユーザーが見つかりません')
    return {'username': user.username,
            'followers': db.query(Follow).filter(Follow.following_id == user.id).count(),
            'following': db.query(Follow).filter(Follow.follower_id == user.id).count(),
            'posts': [serialize_post(db, p) for p in db.query(Post).filter(Post.author_id == user.id).order_by(Post.id.desc()).all()]}

@app.post('/users/{username}/follow')
def toggle_follow(username: str, me=Depends(current_user), db=Depends(db_session)):
    target = db.query(User).filter(User.username == username).first()
    if not target:
        raise HTTPException(404, 'ユーザーが見つかりません')
    if target.id == me.id:
        raise HTTPException(400, '自分はフォローできません')
    follow = db.query(Follow).filter(Follow.follower_id == me.id, Follow.following_id == target.id).first()
    if follow:
        db.delete(follow)
    else:
        db.add(Follow(follower_id=me.id, following_id=target.id))
    db.commit()
    return {'following': not bool(follow)}

@app.get('/following')
def following(me=Depends(current_user), db=Depends(db_session)):
    ids = [f.following_id for f in db.query(Follow).filter(Follow.follower_id == me.id).all()]
    return [u.username for u in db.query(User).filter(User.id.in_(ids)).all()]
