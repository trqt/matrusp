 MATRUSP TCC 2016
================

Branch voltada para o _re_-desenvolvimento do MatrUSP.

| [Versão atual] | [Versão anterior] | [Documentação] |
|:--------------:|:-----------------:|:--------------:|

[Versão atual]: http://bcc.ime.usp.br/matrusp/
[Versão anterior]: http://bcc.ime.usp.br/matrusp_v1/
[Documentação]: http://matrusp.github.io/matrusp/js/docs/

Envolva-se
----------
Veja como [contribuir](https://github.com/matrusp/matrusp/wiki/Contribute) para o repositório.

Ou [navegue](https://github.com/matrusp/matrusp/wiki) pela Wiki do projeto.

Conheça os [contribuidores](CONTRIBUTORS.md) que tornaram o MatrUSP uma realidade.


FAQ
---

 - *Como o MatrUSP funciona?*

    Todo dia à meia-noite, nós rodamos um [script Python](py/parse_usp.py) que varre o JupiterWeb em busca de
    oferecimentos de disciplinas. Os dados são processados e convertidos em um arquivo JSON
    que é salvo em disco no servidor. Quando um usuário acessa o MatrUSP, transmitimos este arquivo.
    Toda a lógica de combinações de disciplinas é feita no cliente.
    
