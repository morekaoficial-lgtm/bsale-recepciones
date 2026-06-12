import streamlit as st
import requests
import re
import pandas as pd
from datetime import datetime
from collections import defaultdict
import io

st.set_page_config(page_title="Bsale - Arqueo de Precios (PDF)", page_icon="📊", layout="wide")

# ==================== CONFIGURACIÓN ====================
ACCESS_TOKEN = "027fa2348b50d5ecd2d2a469f07c464e85cf176d"
BASE_URL = "https://api.bsale.io/v1"
HEADERS = {"access_token": ACCESS_TOKEN, "Content-Type": "application/json"}

# ==================== CSS ====================
st.markdown("""
<style>
.main-header { font-size: 2rem; font-weight: 700; color: #1a1a2e; margin-bottom: 0.5rem; }
.subheader { font-size: 1.1rem; color: #555; margin-bottom: 1.5rem; }
.metric-card { background: #f8f9fa; border-radius: 12px; padding: 1.2rem; border: 1px solid #e9ecef; text-align: center; }
.metric-value { font-size: 1.8rem; font-weight: 700; color: #1a1a2e; }
.metric-label { font-size: 0.85rem; color: #888; margin-top: 0.3rem; }
.section-title { font-size: 1.3rem; font-weight: 600; color: #1a1a2e; margin: 1.5rem 0 0.8rem 0; padding-bottom: 0.5rem; border-bottom: 2px solid #e9ecef; }
.match-row { background: #e8f5e9; border-radius: 8px; padding: 0.8rem; margin: 0.3rem 0; border-left: 4px solid #4caf50; }
.no-match-row { background: #ffebee; border-radius: 8px; padding: 0.8rem; margin: 0.3rem 0; border-left: 4px solid #f44336; }
</style>
""", unsafe_allow_html=True)

# ==================== FUNCIONES ====================
def extract_pdf_data(pdf_file):
    """Extrae modelo y precio del PDF de lista VIP"""
    try:
        import pdfplumber
        with pdfplumber.open(pdf_file) as pdf:
            text = ""
            for page in pdf.pages:
                text += page.extract_text() + "\n"
        
        # Patrón: líneas que empiezan con $precio seguido de modelo
        # Ejemplo: $375.0 A37 20 BOCINA/蓝牙音响 ...
        pattern = r'^\$(\d+(?:\.\d+)?)\s+([A-Z]\d+)\s+(\d+(?:\([^)]*\))?)\s+(.*?)$'
        
        data = []
        lines = text.split('\n')
        for line in lines:
            match = re.match(pattern, line.strip())
            if match:
                price, model, pieces, rest = match.groups()
                # El nombre está hasta el primer caracter chino o slash
                name_parts = rest.split(' ')
                name = name_parts[0] if name_parts else ''
                
                data.append({
                    'modelo': model.strip(),
                    'precio_nuevo': float(price.strip()),
                    'piezas_caja': pieces.strip(),
                    'nombre': name.strip()
                })
        
        return data
    except Exception as e:
        st.error(f"Error leyendo PDF: {e}")
        return []

def get_bsale_products():
    """Obtiene todos los productos de Bsale"""
    products = []
    offset = 0
    
    while True:
        url = f"{BASE_URL}/products.json?limit=50&offset={offset}&expand=variants"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            if resp.status_code != 200:
                break
            data = resp.json()
            items = data.get('items', [])
            if not items:
                break
            
            for item in items:
                prod_id = item.get('id')
                prod_name = item.get('name', '')
                
                # Obtener variantes
                var_url = f"{BASE_URL}/products/{prod_id}/variants.json"
                v_resp = requests.get(var_url, headers=HEADERS, timeout=30)
                if v_resp.status_code == 200:
                    v_data = v_resp.json()
                    variants = v_data.get('items', [])
                    for v in variants:
                        products.append({
                            'product_id': prod_id,
                            'product_name': prod_name,
                            'variant_id': v.get('id'),
                            'variant_desc': v.get('description', ''),
                            'variant_code': v.get('code', ''),
                            'stock': v.get('stock', 0),
                            'costo_promedio': v.get('averageCost', 0)
                        })
            
            if len(items) < 50:
                break
            offset += 50
        except Exception as e:
            st.error(f"Error obteniendo productos: {e}")
            break
    
    return pd.DataFrame(products)

def find_matches(pdf_data, bsale_df):
    """Encuentra coincidencias y calcula nota de crédito solo para stock con costo viejo diferente"""
    matches = []
    
    for pdf_item in pdf_data:
        model = pdf_item['modelo']
        nuevo_precio = pdf_item['precio_nuevo']
        
        # Buscar productos de Bsale que contengan el modelo en el nombre
        matching = bsale_df[bsale_df['product_name'].str.contains(model, case=False, na=False)]
        
        if not matching.empty:
            for _, row in matching.iterrows():
                stock = row['stock']
                costo_viejo = row['costo_promedio']
                
                # Solo procesar si hay stock y hay diferencia de precio
                if stock > 0 and costo_viejo > 0 and costo_viejo != nuevo_precio:
                    valor_inventario_viejo = stock * costo_viejo
                    valor_inventario_nuevo = stock * nuevo_precio
                    diferencia = valor_inventario_nuevo - valor_inventario_viejo
                    
                    matches.append({
                        'modelo_pdf': model,
                        'producto_bsale': row['product_name'],
                        'variante': row['variant_desc'],
                        'codigo': row['variant_code'],
                        'stock': stock,
                        'costo_viejo': costo_viejo,
                        'precio_nuevo': nuevo_precio,
                        'valor_inventario_viejo': valor_inventario_viejo,
                        'valor_inventario_nuevo': valor_inventario_nuevo,
                        'diferencia': diferencia,
                        'necesita_ajuste': True,
                        'match': True
                    })
                elif stock > 0 and costo_viejo > 0 and costo_viejo == nuevo_precio:
                    # Ya tiene el precio nuevo, no necesita ajuste
                    matches.append({
                        'modelo_pdf': model,
                        'producto_bsale': row['product_name'],
                        'variante': row['variant_desc'],
                        'codigo': row['variant_code'],
                        'stock': stock,
                        'costo_viejo': costo_viejo,
                        'precio_nuevo': nuevo_precio,
                        'valor_inventario_viejo': stock * costo_viejo,
                        'valor_inventario_nuevo': stock * nuevo_precio,
                        'diferencia': 0,
                        'necesita_ajuste': False,
                        'match': True
                    })
                else:
                    # Sin stock o sin costo
                    matches.append({
                        'modelo_pdf': model,
                        'producto_bsale': row['product_name'],
                        'variante': row['variant_desc'],
                        'codigo': row['variant_code'],
                        'stock': stock,
                        'costo_viejo': costo_viejo,
                        'precio_nuevo': nuevo_precio,
                        'valor_inventario_viejo': 0,
                        'valor_inventario_nuevo': 0,
                        'diferencia': 0,
                        'necesita_ajuste': False,
                        'match': True
                    })
        else:
            matches.append({
                'modelo_pdf': model,
                'producto_bsale': 'NO ENCONTRADO',
                'variante': '',
                'codigo': '',
                'stock': 0,
                'costo_viejo': 0,
                'precio_nuevo': nuevo_precio,
                'valor_inventario_viejo': 0,
                'valor_inventario_nuevo': 0,
                'diferencia': 0,
                'necesita_ajuste': False,
                'match': False
            })
    
    return pd.DataFrame(matches)

# ==================== UI ====================
st.markdown('<div class="main-header">📊 Bsale — Arqueo de Precios (PDF)</div>', unsafe_allow_html=True)
st.markdown('<div class="subheader">Sube un PDF de lista de precios VIP y compara con tu inventario actual en Bsale</div>', unsafe_allow_html=True)

# Upload PDF
st.markdown('<div class="section-title">📁 Subir PDF de Lista de Precios</div>', unsafe_allow_html=True)
uploaded_file = st.file_uploader("Selecciona el PDF (lista VIP de proveedor)", type=['pdf'])

if uploaded_file:
    # Extraer datos del PDF
    with st.spinner("Extrayendo datos del PDF..."):
        pdf_data = extract_pdf_data(io.BytesIO(uploaded_file.read()))
    
    if pdf_data:
        st.success(f"✅ Se encontraron {len(pdf_data)} modelos en el PDF")
        
        # Mostrar preview del PDF
        df_preview = pd.DataFrame(pdf_data)
        st.dataframe(df_preview, use_container_width=True, height=200)
        
        # Conectar con Bsale
        st.markdown('<div class="section-title">🔍 Conectar con Bsale y Arquear</div>', unsafe_allow_html=True)
        
        if st.button("🔄 Buscar en Bsale y Arquear", type="primary", use_container_width=True):
            with st.spinner("Obteniendo productos de Bsale..."):
                bsale_df = get_bsale_products()
            
            with st.spinner("Haciendo arqueo..."):
                results = find_matches(pdf_data, bsale_df)
            
            # Métricas
            total_pdf = len(pdf_data)
            matched = len(results[results['match'] == True])
            no_match = total_pdf - matched
            
            # Solo contar los que necesitan ajuste real
            necesita_ajuste = results[results['necesita_ajuste'] == True]
            ya_actualizado = results[(results['match'] == True) & (results['necesita_ajuste'] == False) & (results['stock'] > 0)]
            sin_stock = results[(results['match'] == True) & (results['stock'] == 0)]
            
            total_diferencia = necesita_ajuste['diferencia'].sum() if len(necesita_ajuste) > 0 else 0
            
            st.markdown('<div class="section-title">📊 Resumen del Arqueo</div>', unsafe_allow_html=True)
            m1, m2, m3, m4 = st.columns(4)
            m1.markdown(f'<div class="metric-card"><div class="metric-value">{len(necesita_ajuste)}</div><div class="metric-label">Necesitan Ajuste</div></div>', unsafe_allow_html=True)
            m2.markdown(f'<div class="metric-card"><div class="metric-value">{len(ya_actualizado)}</div><div class="metric-label">Ya Actualizados</div></div>', unsafe_allow_html=True)
            m3.markdown(f'<div class="metric-card"><div class="metric-value">{len(sin_stock)}</div><div class="metric-label">Sin Stock</div></div>', unsafe_allow_html=True)
            m4.markdown(f'<div class="metric-card"><div class="metric-value">${total_diferencia:,.2f}</div><div class="metric-label">Diferencia Total</div></div>', unsafe_allow_html=True)
            
            # Tabla detallada
            st.markdown('<div class="section-title">📋 Detalle del Arqueo</div>', unsafe_allow_html=True)
            
            # Separar en tabs
            tab1, tab2, tab3 = st.tabs(["🔴 Necesitan Ajuste", "🟢 Ya Actualizados", "⚪ Sin Stock / No Encontrado"])
            
            with tab1:
                if not necesita_ajuste.empty:
                    df_display = necesita_ajuste.copy()
                    df_display['costo_viejo'] = df_display['costo_viejo'].apply(lambda x: f"${x:,.2f}")
                    df_display['precio_nuevo'] = df_display['precio_nuevo'].apply(lambda x: f"${x:,.2f}")
                    df_display['valor_inventario_viejo'] = df_display['valor_inventario_viejo'].apply(lambda x: f"${x:,.2f}")
                    df_display['valor_inventario_nuevo'] = df_display['valor_inventario_nuevo'].apply(lambda x: f"${x:,.2f}")
                    df_display['diferencia'] = df_display['diferencia'].apply(lambda x: f"${x:,.2f}")
                    
                    st.dataframe(
                        df_display[['modelo_pdf', 'producto_bsale', 'variante', 'stock', 'costo_viejo', 'precio_nuevo', 'valor_inventario_viejo', 'valor_inventario_nuevo', 'diferencia']],
                        use_container_width=True,
                        height=400
                    )
                    
                    # Nota de crédito
                    st.markdown('<div class="section-title">📝 Nota de Crédito (Cálculo)</div>', unsafe_allow_html=True)
                    total_viejo = necesita_ajuste['valor_inventario_viejo'].sum()
                    total_nuevo = necesita_ajuste['valor_inventario_nuevo'].sum()
                    ajuste = total_nuevo - total_viejo
                    
                    st.write(f"**Valor Inventario Actual (costos viejos):** ${total_viejo:,.2f}")
                    st.write(f"**Valor Inventario con Nuevos Precios:** ${total_nuevo:,.2f}")
                    st.write(f"**Ajuste / Nota de Crédito:** ${ajuste:,.2f}")
                    
                    if ajuste > 0:
                        st.info(f"📈 El inventario aumentaría de valor en ${ajuste:,.2f}")
                    elif ajuste < 0:
                        st.info(f"📉 El inventario disminuiría de valor en ${abs(ajuste):,.2f}")
                    else:
                        st.success("✅ No hay diferencia de valor")
                else:
                    st.success("✅ Ningún producto necesita ajuste. Todos tienen precio actualizado o no hay stock.")
            
            with tab2:
                if not ya_actualizado.empty:
                    df_display = ya_actualizado.copy()
                    df_display['costo_viejo'] = df_display['costo_viejo'].apply(lambda x: f"${x:,.2f}")
                    df_display['precio_nuevo'] = df_display['precio_nuevo'].apply(lambda x: f"${x:,.2f}")
                    st.dataframe(
                        df_display[['modelo_pdf', 'producto_bsale', 'variante', 'stock', 'costo_viejo', 'precio_nuevo']],
                        use_container_width=True,
                        height=300
                    )
                    st.info("ℹ️ Estos productos ya tienen el precio nuevo. No necesitan ajuste.")
                else:
                    st.info("No hay productos ya actualizados.")
            
            with tab3:
                df_sin_stock = results[(results['match'] == True) & (results['stock'] == 0)].copy()
                df_no_match = results[results['match'] == False].copy()
                
                if not df_sin_stock.empty:
                    st.write("**Sin Stock:**")
                    st.dataframe(df_sin_stock[['modelo_pdf', 'producto_bsale']], use_container_width=True, height=150)
                
                if not df_no_match.empty:
                    st.write("**No Encontrado en Bsale:**")
                    st.dataframe(df_no_match[['modelo_pdf', 'precio_nuevo']], use_container_width=True, height=150)
                    st.warning("⚠️ Estos modelos no se encontraron en Bsale. Verifica los nombres o códigos.")
            
            # Descargar CSV
            csv = results.to_csv(index=False).encode('utf-8')
            st.download_button(
                "⬇️ Descargar Resultado (CSV)",
                csv,
                f"arqueo_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                "text/csv",
                use_container_width=True
            )
    else:
        st.error("❌ No se pudieron extraer modelos del PDF. Verifica que sea un PDF de lista VIP válido.")

# Footer
st.markdown("""
<div style="text-align: center; color: #888; margin-top: 2rem; padding-top: 1rem; border-top: 1px solid #e9ecef;">
    Conectado a Bsale API | Sube un PDF de lista VIP para comenzar
</div>
""", unsafe_allow_html=True)
