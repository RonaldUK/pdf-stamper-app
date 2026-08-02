import fitz       # PyMuPDF
import cv2
import numpy as np
import os

def buscar_zona_vacia(imagen_gris, ancho_sello_px, alto_sello_px):
    # Convertir la imagen a binaria
    _, binaria = cv2.threshold(imagen_gris, 240, 255, cv2.THRESH_BINARY_INV)
    alto_img, ancho_img = binaria.shape
    paso = 15

    # Búsqueda desde la esquina inferior derecha hacia arriba/izquierda
    for y in range(alto_img - alto_sello_px - 20, 20, -paso):
        for x in range(ancho_img - ancho_sello_px - 20, 20, -paso):
            caja = binaria[y : y + alto_sello_px, x : x + ancho_sello_px]
            pixeles_ocupados = cv2.countNonZero(caja)
            
            if pixeles_ocupados < (ancho_sello_px * alto_sello_px * 0.002):
                return x, y

    # Si no encuentra espacio libre, posición de respaldo en la esquina inferior derecha
    return ancho_img - ancho_sello_px - 30, alto_img - alto_sello_px - 30


def procesar_pdf_completo(ruta_pdf, ruta_sello_png):
    doc = fitz.open(ruta_pdf)
    total_paginas = len(doc)
    print(f"📄 Procesando PDF completo con {total_paginas} página(s)...")

    # 1. Recorrer TODAS las páginas del PDF
    for i in range(total_paginas):
        pagina = doc[i]
        print(f"\n🔍 Analizando Página {i + 1} de {total_paginas}...")

        # Renderizar la página a imagen para visión artificial
        pixmap = pagina.get_pixmap(dpi=150)
        img_np = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.h, pixmap.w, pixmap.n).copy()
        imagen_gris = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY) if pixmap.n >= 3 else img_np

        # Tamaños del sello en píxeles (para búsqueda a 150 DPI)
        ancho_sello_px = 250
        alto_sello_px = 120

        # Encontrar la zona libre
        x_px, y_px = buscar_zona_vacia(imagen_gris, ancho_sello_px, alto_sello_px)
        print(f"✅ Zona detectada en Página {i + 1} -> X: {x_px}, Y: {y_px}")

        # Convertir píxeles a Puntos del PDF original
        factor_x = pagina.rect.width / pixmap.w
        factor_y = pagina.rect.height / pixmap.h

        pdf_x = x_px * factor_x
        pdf_y = y_px * factor_y
        pdf_ancho = ancho_sello_px * factor_x
        pdf_alto = alto_sello_px * factor_y

        # 2. ESTAMPAR EL SELLO PNG EN EL PDF
        if os.path.exists(ruta_sello_png):
            rect_sello = fitz.Rect(pdf_x, pdf_y, pdf_x + pdf_ancho, pdf_y + pdf_alto)
            pagina.insert_image(rect_sello, filename=ruta_sello_png)
            print(f"Stamp colocado en Página {i + 1}.")
        else:
            print(f"⚠️ No se encontró la imagen '{ruta_sello_png}'. Solo se calcularon coordenadas.")

    # 3. Guardar el nuevo PDF sellado
    pdf_salida = ruta_pdf.replace(".pdf", "_SELLADO.pdf")
    doc.save(pdf_salida)
    doc.close()
    
    print(f"\n🎉 ¡Proceso terminado! Archivo generado: '{pdf_salida}'")


# --- EJECUCIÓN ---
if __name__ == "__main__":
    archivo_pdf = "mi_plano.pdf"
    imagen_sello = "sello_firma.png"  # Coloca aquí un PNG transparente con tu sello/firma
    
    try:
        procesar_pdf_completo(archivo_pdf, imagen_sello)
    except Exception as e:
        print(f"❌ Error durante el procesamiento: {e}")