# googletrans-py

This is a fork of the [googletrans](https://github.com/ssut/py-googletrans) that fix issues about detecting language and translations.

In fact, the v3.1.0a0 had problems to detect some languages like chinese traditional, and the 4.0.0rc1 had problems to translate small words like [this issue](https://github.com/ssut/py-googletrans/issues/394)

> **Note**: I have no intention to add features or to maintain this project. This is a temporary solution to a temporary a problem, hopefully. If you have ideas to maintain this project (like allow more recent versions of httpx), your help is welcome

## Installation

### PyPI

Actually I have nopt posted this project on pypi. The better way is to use Repository, or to install googletrans==3.1.0a0 and change the files by the ones in `googletrans` folder

### Repository

You can also install the project directly from this repository.

```shell
pip install git+https://github.com/UnBonWhisky/googletrans.git
```

## How to use

This version is actually an asynchronous version of googletrans, with some functions from the version 3.1.0a0 and some functions from the version 4.0.0rc1. I have adapted it using my knowledges so it may not be the best code you can see.

Here is an example code of how to use it :
```py
from googletrans import Translator
import asyncio

trad = Translator()

async def main():
    translation = await trad.translate("Here is my code example", dest="fr")
    print(translation.text) # Voici mon exemple de code
    translation = await trad.detect("Un texte français")
    print(translation.lang) # fr

if __name__ == "__main__":
    asyncio.run(main())
```

It may have issues when using `trad.detect()` (like `json.decoder.JSONDecodeError` or `TypeError`, so I recommand to use it like this :
```py
try :
    translation = await trad.detect("Un texte français")
except :
    translation = await trad.detect_legacy("Un texte français")
```

## Credits

Original Author - [Suhun Han](https://github.com/ssut)

Original Repository - [googletrans](https://github.com/ssut/py-googletrans)
