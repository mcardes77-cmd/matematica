from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_file
import json, random, sqlite3, os
from datetime import datetime
from io import BytesIO
from openpyxl import Workbook
from openai import OpenAI

app = Flask(__name__)
app.secret_key = "matharena-master-key"
DB_NAME = "matharena.db"
QUESTION_FILE = "perguntas.json"

JOGOS = {
    "1":"Operações Básicas","2":"Porcentagem","3":"Regra de Três","4":"Geometria Básica",
    "5":"Operações - Desafio Rápido","6":"Porcentagem no Dia a Dia","7":"Regra de Três Inversa","8":"Geometria Espacial Básica",
    "9":"Problemas com Dinheiro","10":"Frações e Decimais","11":"Razão e Proporção","12":"Múltiplos e Divisores",
    "13":"Potenciação e Raiz","14":"Equações Simples","15":"Unidades de Medida","16":"Desafio Misto"
}

def load_questions():
    with open(QUESTION_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_questions(data):
    with open(QUESTION_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_conn():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS partidas (id INTEGER PRIMARY KEY AUTOINCREMENT, tema TEXT NOT NULL, dificuldade TEXT NOT NULL, tempo_segundos INTEGER NOT NULL, criado_em TEXT NOT NULL)")
    cur.execute("CREATE TABLE IF NOT EXISTS equipes (id INTEGER PRIMARY KEY AUTOINCREMENT, partida_id INTEGER NOT NULL, nome TEXT NOT NULL, pontos INTEGER NOT NULL DEFAULT 0, FOREIGN KEY(partida_id) REFERENCES partidas(id))")
    conn.commit()
    conn.close()

init_db()

@app.route("/")
def index():
    return render_template("index.html", jogos=JOGOS)

@app.route("/editor")
def editor():
    return render_template("editor.html", jogos=JOGOS)

@app.route("/iniciar", methods=["POST"])
def iniciar():
    nomes = [request.form.get(f"equipe_{i}", "").strip() for i in range(1, 9)]
    nomes = [n for n in nomes if n] or ["Equipe 1", "Equipe 2", "Equipe 3", "Equipe 4"]
    jogo_id = request.form.get("jogo_id", "1")
    tempo_segundos = int(request.form.get("tempo_segundos", "60"))
    dificuldade = request.form.get("dificuldade", "misto")

    banco = load_questions()
    pool = list(banco[jogo_id])
    if dificuldade != "misto":
        filtradas = [q for q in pool if q.get("dificuldade") == dificuldade]
        if filtradas:
            pool = filtradas
    random.shuffle(pool)

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO partidas (tema, dificuldade, tempo_segundos, criado_em) VALUES (?, ?, ?, ?)", (JOGOS.get(jogo_id, "Jogo"), dificuldade, tempo_segundos, datetime.now().isoformat()))
    partida_id = cur.lastrowid

    equipes = []
    for nome in nomes:
        cur.execute("INSERT INTO equipes (partida_id, nome, pontos) VALUES (?, ?, ?)", (partida_id, nome, 0))
        equipes.append({"id": cur.lastrowid, "nome": nome, "pontos": 0})

    conn.commit()
    conn.close()

    session["equipes"] = equipes
    session["turno"] = 0
    session["jogo_id"] = jogo_id
    session["tempo_segundos"] = tempo_segundos
    session["dificuldade"] = dificuldade
    session["steal_mode"] = False
    session["fila_perguntas"] = pool
    session["indice_pergunta"] = 0
    return redirect(url_for("jogo"))

@app.route("/jogo")
def jogo():
    if "equipes" not in session:
        return redirect(url_for("index"))
    return render_template("jogo.html", jogo_nome=JOGOS.get(session["jogo_id"], "Jogo"), tempo_segundos=session.get("tempo_segundos", 60), dificuldade=session.get("dificuldade", "misto"))

@app.route("/api/estado")
def estado():
    if "equipes" not in session:
        return jsonify({"erro":"Sessão não iniciada"}), 400
    equipes = session["equipes"]
    turno = session["turno"]
    fila = session.get("fila_perguntas", [])
    indice = session.get("indice_pergunta", 0)
    total = len(fila)
    restante = max(total - indice, 0)
    return jsonify({"equipes": sorted(equipes, key=lambda x: x["pontos"], reverse=True), "equipe_da_vez": session["equipes"][turno]["nome"], "tempo_segundos": session.get("tempo_segundos", 60), "dificuldade": session.get("dificuldade", "misto"), "steal_mode": session.get("steal_mode", False), "restante": restante, "total": total})

@app.route("/api/pergunta")
def pergunta():
    if "fila_perguntas" not in session:
        return jsonify({"erro":"Sessão não iniciada"}), 400
    fila = session["fila_perguntas"]
    indice = session.get("indice_pergunta", 0)
    if indice >= len(fila):
        random.shuffle(fila)
        indice = 0
    pergunta = fila[indice]
    session["indice_pergunta"] = indice + 1
    session["pergunta_atual"] = pergunta
    session["fila_perguntas"] = fila
    return jsonify(pergunta)

def update_points(equipe):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE equipes SET pontos = ? WHERE id = ?", (equipe["pontos"], equipe["id"]))
    conn.commit()
    conn.close()

@app.route("/api/passar", methods=["POST"])
def passar():
    if "equipes" not in session:
        return jsonify({"erro":"Sessão inválida"}), 400
    equipes = session["equipes"]
    turno = session["turno"]
    equipes[turno]["pontos"] -= 2
    update_points(equipes[turno])
    session["turno"] = (turno + 1) % len(equipes)
    session["steal_mode"] = True
    session["equipes"] = equipes
    return jsonify({"ok": True, "equipes": sorted(equipes, key=lambda x: x["pontos"], reverse=True), "proxima_equipe": session["equipes"][session["turno"]]["nome"], "mensagem": "Equipe passou e perdeu 2 pontos. A próxima equipe pode responder."})

@app.route("/api/responder", methods=["POST"])
def responder():
    if "equipes" not in session or "pergunta_atual" not in session:
        return jsonify({"erro":"Sessão inválida"}), 400
    data = request.get_json()
    alternativa = data.get("alternativa")
    correta = session["pergunta_atual"]["correta"]
    equipes = session["equipes"]
    turno = session["turno"]
    steal_mode = session.get("steal_mode", False)
    acertou = alternativa == correta
    delta = 0
    if acertou:
        delta = 10
        equipes[turno]["pontos"] += delta
    elif steal_mode:
        delta = -5
        equipes[turno]["pontos"] += delta
    update_points(equipes[turno])
    session["turno"] = (turno + 1) % len(equipes)
    session["steal_mode"] = False
    session["equipes"] = equipes
    return jsonify({"acertou": acertou, "correta": correta, "delta": delta, "steal_mode": steal_mode, "equipes": sorted(equipes, key=lambda x: x["pontos"], reverse=True), "proxima_equipe": session["equipes"][session["turno"]]["nome"]})

@app.route("/api/historico")
def historico():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, tema, dificuldade, tempo_segundos, criado_em FROM partidas ORDER BY id DESC LIMIT 50")
    partidas = [dict(row) for row in cur.fetchall()]
    for partida in partidas:
        cur.execute("SELECT nome, pontos FROM equipes WHERE partida_id = ? ORDER BY pontos DESC, nome ASC", (partida["id"],))
        partida["equipes"] = [dict(row) for row in cur.fetchall()]
    conn.close()
    return jsonify(partidas)

@app.route("/api/excluir_partida/<int:partida_id>", methods=["POST"])
def excluir_partida(partida_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM equipes WHERE partida_id = ?", (partida_id,))
    cur.execute("DELETE FROM partidas WHERE id = ?", (partida_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})

@app.route("/exportar/xlsx/<int:partida_id>")
def exportar_xlsx(partida_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, tema, dificuldade, tempo_segundos, criado_em FROM partidas WHERE id = ?", (partida_id,))
    partida = cur.fetchone()
    if not partida:
        conn.close()
        return "Partida não encontrada", 404
    cur.execute("SELECT nome, pontos FROM equipes WHERE partida_id = ? ORDER BY pontos DESC, nome ASC", (partida_id,))
    equipes = cur.fetchall()
    conn.close()
    wb = Workbook()
    ws = wb.active
    ws.title = "Ranking"
    ws.append(["Partida", partida["id"]]); ws.append(["Tema", partida["tema"]]); ws.append(["Dificuldade", partida["dificuldade"]]); ws.append(["Tempo (s)", partida["tempo_segundos"]]); ws.append(["Criado em", partida["criado_em"]]); ws.append([])
    ws.append(["Posição", "Equipe", "Pontos"])
    for idx, equipe in enumerate(equipes, start=1):
        ws.append([idx, equipe["nome"], equipe["pontos"]])
    bio = BytesIO(); wb.save(bio); bio.seek(0)
    return send_file(bio, as_attachment=True, download_name=f"ranking_partida_{partida_id}.xlsx", mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@app.route("/api/questions/<jogo_id>")
def get_questions(jogo_id):
    banco = load_questions()
    return jsonify(banco.get(jogo_id, []))

@app.route("/api/questions/<jogo_id>", methods=["POST"])
def add_question(jogo_id):
    banco = load_questions()
    data = request.get_json()
    nova = {"dificuldade": data["dificuldade"], "enunciado": data["enunciado"], "alternativas": data["alternativas"], "correta": data["correta"]}
    banco.setdefault(jogo_id, []).append(nova)
    save_questions(banco)
    return jsonify({"ok": True})

@app.route("/api/questions/<jogo_id>/<int:idx>", methods=["DELETE"])
def delete_question(jogo_id, idx):
    banco = load_questions()
    if jogo_id in banco and 0 <= idx < len(banco[jogo_id]):
        banco[jogo_id].pop(idx)
        save_questions(banco)
        return jsonify({"ok": True})
    return jsonify({"erro":"Pergunta não encontrada"}), 404

@app.route("/api/gerar_ia/<jogo_id>", methods=["POST"])
def gerar_ia(jogo_id):
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return jsonify({"erro":"Configure a variável OPENAI_API_KEY para ativar a IA."}), 400
    tema = JOGOS.get(jogo_id, "Matemática")
    data = request.get_json() or {}
    dificuldade = data.get("dificuldade", "medio")
    client = OpenAI(api_key=api_key)
    prompt = f"""Gere 1 pergunta de múltipla escolha para alunos do ensino fundamental sobre {tema}.
Dificuldade: {dificuldade}.
Responda APENAS em JSON com as chaves:
dificuldade, enunciado, alternativas, correta

Regras:
- 4 alternativas
- a correta deve existir exatamente dentro da lista alternativas
- linguagem clara
- sem explicação extra"""
    response = client.responses.create(model="gpt-5.2", input=prompt)
    text = response.output_text.strip()
    try:
        return jsonify(json.loads(text))
    except Exception:
        return jsonify({"erro":"A IA não retornou JSON válido.","raw":text}), 400

@app.route("/reiniciar", methods=["POST"])
def reiniciar():
    session.clear()
    return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(debug=True)
