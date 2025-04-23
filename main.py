from flask import Flask, render_template, request, jsonify, redirect, url_for
import os
import logging
import xml.etree.ElementTree as ET
from werkzeug.utils import secure_filename
from parser import process_xml_and_scan
from models import db, Comparison, Product, KaspiResult
import json
from dotenv import load_dotenv

# Load environment variables from .env file if it exists
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.DEBUG, 
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "kaspi-price-comparison-tool")
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # Limit upload size to 16MB

# Configure database
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get("DATABASE_URL")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    "pool_recycle": 300,
    "pool_pre_ping": True,
}

# Initialize database
db.init_app(app)

# Create database tables if they don't exist
with app.app_context():
    db.create_all()

@app.route('/')
def index():
    """Serve the upload page"""
    return render_template('index.html')

@app.route('/example.xml')
def example_xml():
    """Generate example XML file with sample products"""
    xml_content = """<?xml version="1.0" encoding="UTF-8"?>
<products>
  <item>
    <sku>ABC-123</sku>
    <model>Samsung Galaxy S21</model>
    <price>349990</price>
    <stock>10</stock>
  </item>
  <item>
    <sku>ABC-124</sku>
    <model>Apple iPhone 13</model>
    <price>419990</price>
    <stock>5</stock>
  </item>
  <item>
    <sku>ABC-125</sku>
    <model>Xiaomi Mi 11</model>
    <price>189990</price>
    <stock>15</stock>
  </item>
</products>"""
    response = app.response_class(
        response=xml_content,
        status=200,
        mimetype='application/xml'
    )
    response.headers["Content-Disposition"] = "attachment; filename=example.xml"
    return response

@app.route('/scan', methods=['POST'])
def upload_and_scan_file():
    """Process uploaded XML file and return comparison results"""
    if 'file' not in request.files:
        logger.error("No file part in the request")
        return jsonify({"error": "No file part"}), 400
        
    file = request.files['file']
    
    if file.filename == '':
        logger.error("No file selected")
        return jsonify({"error": "No file selected"}), 400
        
    if not file.filename.endswith('.xml'):
        logger.error(f"Invalid file type: {file.filename}")
        return jsonify({"error": "Only XML files are supported"}), 400
    
    try:
        # Read the content
        content = file.read()
        
        # Validate XML structure
        try:
            ET.fromstring(content)
        except ET.ParseError as e:
            logger.error(f"XML parse error: {str(e)}")
            return jsonify({"error": "Invalid XML format"}), 400
        
        # Process the XML content and compare with Kaspi
        # Ограничиваем обработку 50 товарами для предотвращения перегрузки памяти и сбоев сервера
        results = process_xml_and_scan(content, max_items=50)
        logger.info(f"Processing completed. Found {len(results)} products. Limited to max 50 items.")
        
        # Сохраняем результаты в базу данных
        comparison = Comparison(
            filename=secure_filename(file.filename),
            products_count=len(results)
        )
        db.session.add(comparison)
        
        # Добавляем продукты
        for product_data in results:
            product = Product(
                comparison=comparison,
                sku=product_data.get('sku', ''),
                model=product_data.get('model', ''),
                our_price=float(product_data.get('our_price', 0)),
                stock=int(product_data.get('stock', 0))
            )
            db.session.add(product)
            
            # Добавляем результаты Kaspi для каждого продукта
            for kaspi_result in product_data.get('kaspi_results', []):
                kaspi_price = kaspi_result.get('kaspi_price', '0')
                
                # Преобразуем строковую цену в число
                try:
                    kaspi_price_float = float(kaspi_price.replace(',', '.').strip())
                except (ValueError, TypeError):
                    logger.warning(f"Invalid price format: {kaspi_price}")
                    kaspi_price_float = 0
                
                result = KaspiResult(
                    product=product,
                    kaspi_name=kaspi_result.get('kaspi_name', ''),
                    kaspi_price=kaspi_price_float,
                    price_difference_percent=kaspi_result.get('price_difference_percent')
                )
                
                # Сохраняем список продавцов как JSON
                sellers = kaspi_result.get('sellers', [])
                result.set_sellers(sellers)
                
                db.session.add(result)
        
        # Коммитим изменения в базу данных
        db.session.commit()
        logger.info(f"Saved comparison #{comparison.id} to database with {len(results)} products")
        
        # Добавляем id сравнения в результаты для использования в интерфейсе
        for result in results:
            result['comparison_id'] = comparison.id
            
        return jsonify(results)
    
    except Exception as e:
        # В случае ошибки делаем rollback
        db.session.rollback()
        logger.error(f"Error processing file: {str(e)}")
        return jsonify({"error": f"Error processing file: {str(e)}"}), 500

@app.route('/results')
def results():
    """Serve the results page"""
    return render_template('results.html')

@app.route('/history')
def history():
    """Serve the history page with all saved comparisons"""
    comparisons = Comparison.query.order_by(Comparison.created_at.desc()).all()
    return render_template('history.html', comparisons=comparisons)

@app.route('/api/comparisons')
def get_comparisons():
    """Get list of all comparisons"""
    comparisons = Comparison.query.order_by(Comparison.created_at.desc()).all()
    return jsonify([c.to_dict(include_products=False) for c in comparisons])

@app.route('/api/comparison/<int:comparison_id>')
def get_comparison(comparison_id):
    """Get details of a specific comparison"""
    comparison = Comparison.query.get_or_404(comparison_id)
    
    # Ограничиваем выборку до 50 товаров для предотвращения ошибок памяти
    products_query = Product.query.filter_by(comparison_id=comparison_id).limit(50)
    products = products_query.all()
    
    # Собираем данные вручную для оптимизации
    result = comparison.to_dict(include_products=False)
    result['products'] = []
    
    for product in products:
        product_data = product.to_dict()
        result['products'].append(product_data)
    
    return jsonify(result)

# Add error handlers
@app.errorhandler(413)
def request_entity_too_large(error):
    return jsonify({"error": "File too large. Maximum size is 16MB"}), 413

@app.errorhandler(500)
def internal_server_error(error):
    return jsonify({"error": "Internal server error"}), 500

# Получаем значения из переменных окружения

# Получаем максимальное количество обрабатываемых товаров из переменных окружения
MAX_ITEMS_TO_PROCESS = int(os.environ.get("MAX_ITEMS_TO_PROCESS", 50))

# Создаем новый маршрут с другим именем функции
@app.route('/scan-with-config', methods=['POST'])
def scan_with_config():
    """Process uploaded XML file and return comparison results using environment config"""
    if 'file' not in request.files:
        logger.error("No file part in the request")
        return jsonify({"error": "No file part"}), 400
        
    file = request.files['file']
    
    if file.filename == '':
        logger.error("No file selected")
        return jsonify({"error": "No file selected"}), 400
        
    if not file.filename.endswith('.xml'):
        logger.error(f"Invalid file type: {file.filename}")
        return jsonify({"error": "Only XML files are supported"}), 400
    
    try:
        # Read the content
        content = file.read()
        
        # Validate XML structure
        try:
            ET.fromstring(content)
        except ET.ParseError as e:
            logger.error(f"XML parse error: {str(e)}")
            return jsonify({"error": "Invalid XML format"}), 400
        
        # Process the XML content and compare with Kaspi
        # Используем переменную окружения для ограничения количества товаров
        results = process_xml_and_scan(content, max_items=MAX_ITEMS_TO_PROCESS)
        logger.info(f"Processing completed. Found {len(results)} products. Limited to max {MAX_ITEMS_TO_PROCESS} items.")
        
        # Сохраняем результаты в базу данных
        comparison = Comparison(
            filename=secure_filename(file.filename),
            products_count=len(results)
        )
        db.session.add(comparison)
        
        # Добавляем продукты
        for product_data in results:
            product = Product(
                comparison=comparison,
                sku=product_data.get('sku', ''),
                model=product_data.get('model', ''),
                our_price=float(product_data.get('our_price', 0)),
                stock=int(product_data.get('stock', 0))
            )
            db.session.add(product)
            
            # Добавляем результаты Kaspi для каждого продукта
            for kaspi_result in product_data.get('kaspi_results', []):
                kaspi_price = kaspi_result.get('kaspi_price', '0')
                
                # Преобразуем строковую цену в число
                try:
                    kaspi_price_float = float(kaspi_price.replace(',', '.').strip())
                except (ValueError, TypeError):
                    logger.warning(f"Invalid price format: {kaspi_price}")
                    kaspi_price_float = 0
                
                result = KaspiResult(
                    product=product,
                    kaspi_name=kaspi_result.get('kaspi_name', ''),
                    kaspi_price=kaspi_price_float,
                    price_difference_percent=kaspi_result.get('price_difference_percent', 0)
                )
                
                # Сохраняем список продавцов как JSON
                sellers = kaspi_result.get('sellers', [])
                result.set_sellers(sellers)
                
                # Сохраняем URL для проверки на Kaspi
                if 'kaspi_url' in kaspi_result:
                    # Добавление URL в данные
                    result.kaspi_url = kaspi_result.get('kaspi_url', '')
                
                db.session.add(result)
        
        # Коммитим изменения в базу данных
        db.session.commit()
        logger.info(f"Saved comparison #{comparison.id} to database with {len(results)} products")
        
        # Добавляем id сравнения в результаты для использования в интерфейсе
        for result in results:
            result['comparison_id'] = comparison.id
            
        return jsonify(results)
    
    except Exception as e:
        # В случае ошибки делаем rollback
        db.session.rollback()
        logger.error(f"Error processing file: {str(e)}")
        return jsonify({"error": f"Error processing file: {str(e)}"}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    debug_mode = os.environ.get("DEBUG", "False").lower() == "true"
    app.run(host='0.0.0.0', port=port, debug=debug_mode)