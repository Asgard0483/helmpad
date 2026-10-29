"""Meldungen mit Code: Der deutsche Text dient Kommandozeile und Bericht, Code + Werte der
Oberfläche, die daraus die Meldung in der gewählten Sprache baut."""


def msg(code, text, **args):
    return {"code": code, "args": args, "text": text}


def texts(messages):
    return [m["text"] for m in messages]
