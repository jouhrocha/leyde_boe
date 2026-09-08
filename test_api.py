#!/usr/bin/env python3
import sys
sys.path.insert(0, '/media/user/d48e5428-e66b-4a12-902d-02f018700477/LEYDE_BOE')

from server import app
import json

def test_all():
    with app.test_client() as client:
        print("="*50)
        print("PROBANDO API LEYDE BOE")
        print("="*50)
        
        # Test 1: Stats
        print("\n[1] GET /api/stats")
        r = client.get('/api/stats')
        print(f"  Status: {r.status_code}")
        data = r.get_json()
        print(f"  Articulos: {data.get('articulos', 'N/A')}")
        print(f"  Leyes: {data.get('leyes', 'N/A')}")
        
        # Test 2: Leyes
        print("\n[2] GET /api/leyes?limit=3")
        r = client.get('/api/leyes?limit=3')
        print(f"  Status: {r.status_code}")
        leyes = r.get_json()
        print(f"  Total leyes devueltas: {len(leyes)}")
        if leyes:
            print(f"  Primera: {leyes[0].get('nombre', 'N/A')[:50]}...")
        
        # Test 3: Buscar
        print("\n[3] GET /api/buscar?q=contrato&limit=2")
        r = client.get('/api/buscar?q=contrato&limit=2')
        print(f"  Status: {r.status_code}")
        lineas = r.data.decode().strip().split('\n')
        print(f"  Lineas recibidas: {len(lineas)}")
        for linea in lineas[:2]:
            try:
                obj = json.loads(linea)
                if obj.get('type') == 'meta':
                    print(f"  Meta - Total resultados: {obj.get('total')}")
                elif obj.get('type') == 'result':
                    print(f"  Resultado: {obj.get('ley', 'N/A')[:40]}...")
            except:
                pass
        
        # Test 4: Sugerencias
        print("\n[4] GET /api/sugerencias?q=art")
        r = client.get('/api/sugerencias?q=art')
        print(f"  Status: {r.status_code}")
        sugerencias = r.get_json()
        print(f"  Sugerencias: {sugerencias[:3]}")
        
        # Test 5: Registro
        print("\n[5] POST /api/auth/register")
        r = client.post('/api/auth/register', 
            json={'username': 'test_user', 'email': 'test@test.com', 'password': 'test12345'})
        print(f"  Status: {r.status_code}")
        print(f"  Response: {r.get_json()}")
        
        # Test 6: Login
        print("\n[6] POST /api/auth/login")
        r = client.post('/api/auth/login',
            json={'email': 'test@test.com', 'password': 'test12345'})
        print(f"  Status: {r.status_code}")
        login_data = r.get_json()
        print(f"  Success: {login_data.get('success')}")
        
        # Test 7: Auth me
        print("\n[7] GET /api/auth/me")
        r = client.get('/api/auth/me')
        print(f"  Status: {r.status_code}")
        print(f"  Autenticado: {r.get_json().get('authenticated')}")
        
        # Test 8: Pagina principal
        print("\n[8] GET / (pagina principal)")
        r = client.get('/')
        print(f"  Status: {r.status_code}")
        print(f"  Content-Length: {len(r.data)} bytes")
        
        # Test 9: Pagina login
        print("\n[9] GET /login.html")
        r = client.get('/login.html')
        print(f"  Status: {r.status_code}")
        print(f"  Content-Length: {len(r.data)} bytes")
        
        print("\n" + "="*50)
        print("TODAS LAS PRUEBAS COMPLETADAS")
        print("="*50)

if __name__ == '__main__':
    test_all()
