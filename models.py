import os
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import Column, Integer, String, Float, DateTime, Text, ForeignKey, JSON
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import json
from datetime import datetime

# Инициализация базы данных
class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)

class Comparison(db.Model):
    __tablename__ = 'comparisons'
    
    id = Column(Integer, primary_key=True)
    filename = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=func.now())
    products_count = Column(Integer, default=0)
    
    # Связь один-ко-многим с товарами
    products = relationship("Product", back_populates="comparison", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<Comparison id={self.id}, filename={self.filename}, products={self.products_count}>"
    
    def to_dict(self, include_products=True):
        """Convert comparison to dictionary for API responses"""
        result = {
            "id": self.id,
            "filename": self.filename,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "products_count": self.products_count
        }
        
        if include_products:
            result["products"] = [product.to_dict() for product in self.products]
            
        return result

class Product(db.Model):
    __tablename__ = 'products'
    
    id = Column(Integer, primary_key=True)
    comparison_id = Column(Integer, ForeignKey('comparisons.id'))
    sku = Column(String(100))
    model = Column(String(255))
    our_price = Column(Float)
    stock = Column(Integer)
    
    # Связь многие-к-одному с сравнением
    comparison = relationship("Comparison", back_populates="products")
    
    # Связь один-ко-многим с результатами касиспи
    kaspi_results = relationship("KaspiResult", back_populates="product", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<Product id={self.id}, sku={self.sku}, model={self.model}>"
    
    def to_dict(self):
        return {
            "id": self.id,
            "sku": self.sku,
            "model": self.model,
            "our_price": self.our_price,
            "stock": self.stock,
            "kaspi_results": [result.to_dict() for result in self.kaspi_results]
        }

class KaspiResult(db.Model):
    __tablename__ = 'kaspi_results'
    
    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey('products.id'))
    kaspi_name = Column(String(255))
    kaspi_price = Column(Float)
    price_difference_percent = Column(Float)
    sellers = Column(Text) # Хранится как JSON-строка
    kaspi_url = Column(String(500), nullable=True) # URL для проверки на Kaspi.kz
    
    # Связь многие-к-одному с товаром
    product = relationship("Product", back_populates="kaspi_results")
    
    def __repr__(self):
        return f"<KaspiResult id={self.id}, product_id={self.product_id}, price={self.kaspi_price}>"
    
    def get_sellers(self):
        if not self.sellers:
            return []
        try:
            return json.loads(self.sellers)
        except json.JSONDecodeError:
            return []
    
    def set_sellers(self, sellers_list):
        self.sellers = json.dumps(sellers_list) if sellers_list else "[]"
    
    def to_dict(self):
        return {
            "id": self.id,
            "kaspi_name": self.kaspi_name,
            "kaspi_price": self.kaspi_price,
            "price_difference_percent": self.price_difference_percent,
            "sellers": self.get_sellers(),
            "kaspi_url": self.kaspi_url if hasattr(self, 'kaspi_url') else None
        }