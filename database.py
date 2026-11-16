from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
DATABASE_URL = 'sqlite:///' + str(Path(__file__).resolve().parent / 'sns.db').replace('\\', '/')
engine = create_engine(DATABASE_URL, connect_args={'check_same_thread': False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
