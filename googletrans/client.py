# -*- coding: utf-8 -*-
"""
A Translation module.

You can translate text using this module.
"""
import aiorwlock
import random
import re
import json

import aiohttp
from aiohttp_socks import ProxyConnector

from contextlib import asynccontextmanager

from googletrans import urls, utils
from googletrans.gtoken import TokenAcquirer
from googletrans.constants import (
    DEFAULT_CLIENT_SERVICE_URLS,
    DEFAULT_USER_AGENT, LANGCODES, LANGUAGES, SPECIAL_CASES,
    DEFAULT_RAISE_EXCEPTION, DUMMY_DATA
)
from googletrans.models import Translated, Detected, TranslatedPart, Translate_to_Detect, RateLimitError

EXCLUDES = ('en', 'ca', 'fr')

RPC_ID = 'MkEWBc'


class Translator:
    """Google Translate ajax API implementation class

    You have to create an instance of Translator to use this API

    :param service_urls: google translate url list. URLs will be used randomly.
                         For example ``['translate.google.com', 'translate.google.co.kr']``
                         To preferably use the non webapp api, service url should be translate.googleapis.com
    :type service_urls: a sequence of strings

    :param user_agent: the User-Agent header to send when making requests.
    :type user_agent: :class:`str`

    :param proxies: proxies configuration.
                    Dictionary mapping protocol or protocol and host to the URL of the proxy
                    For example ``{'http': 'foo.bar:3128', 'http://host.name': 'foo.bar:4012'}``
    :type proxies: dictionary

    :param timeout: Definition of timeout for httpx library.
                    Will be used for every request.
    :type timeout: number or a double of numbers
    :param proxy:  proxies configuration.
                    List mapping socks5 and http(s) host to the URL of the proxy
                    For example ``socks5://foo.bar:1080`` or ``https://foo.bar:8080``
    :param raise_exception: if `True` then raise exception if smth will go wrong
    :type raise_exception: boolean
    """

    def __init__(self, service_urls=DEFAULT_CLIENT_SERVICE_URLS, user_agent=DEFAULT_USER_AGENT,
                 raise_exception=DEFAULT_RAISE_EXCEPTION,
                 proxy: str = None,
                 proxy_auth: tuple = None,
                 timeout: aiohttp.ClientTimeout = None,
                 connector_limit: int = 100):

        connector = None
        self.connector_limit = connector_limit
        self.proxy = None
        self.proxy_auth = None
        self._use_proxy_connector = False
        
        self.rwlock = aiorwlock.RWLock(fast=True)
        
        if proxy is not None:
            if proxy.startswith('socks5') or proxy.startswith('socks4'):
                connector = ProxyConnector.from_url(proxy)
                self._use_proxy_connector = True
            else:
                # HTTP/HTTPS proxy
                self.proxy = proxy
                self.proxy_auth = aiohttp.BasicAuth(proxy_auth[0], proxy_auth[1]) if proxy_auth else None
        
        if connector is None:
            connector = aiohttp.TCPConnector(limit=self.connector_limit)
        
        self.connector = connector
        self.timeout = timeout if timeout is not None else aiohttp.ClientTimeout(total=30)
        self.user_agent = user_agent
        self._proxy_url = proxy  # Store for recreating proxy connector if needed
        
        self._session = aiohttp.ClientSession(
            connector=self.connector,
            connector_owner=True,
            timeout=self.timeout,
            headers={'User-Agent': self.user_agent}
        )

        if (service_urls is not None):
            #default way of working: use the defined values from user app
            self.service_urls = service_urls
            self.client_type = 'webapp'
            self.token_acquirer = None  # Will be created when session is initialized

            #if we have a service url pointing to client api we force the use of it as defaut client
            for _ in enumerate(service_urls):
                api_type = re.search('googleapis',service_urls[0])
                if (api_type):
                    self.service_urls = ['translate.googleapis.com']
                    self.client_type = 'gtx'
                    break
        else:
            self.service_urls = ['translate.google.com']
            self.client_type = 'webapp'
            self.token_acquirer = None  # Will be created when session is initialized
            
        if self.client_type == 'webapp':
            self.token_acquirer = TokenAcquirer(
                session=self._session, host=self.service_urls[0])
        self.raise_exception = raise_exception
    
    @asynccontextmanager
    async def _get_session(self):
        """Get or create aiohttp session"""
        async with self.rwlock.reader_lock:
            yield self._session
    
    def __del__(self):
        """Cleanup when object is garbage collected to prevent unclosed warnings"""
        try:
            # Close session's connector synchronously
            if hasattr(self, '_session') and self._session is not None:
                if hasattr(self._session, '_connector') and self._session._connector is not None:
                    if not self._session._connector.closed:
                        try:
                            self._session._connector._close()
                        except Exception :
                            pass
            
            # Close standalone connector reference
            if hasattr(self, 'connector') and self.connector is not None:
                if not self.connector.closed:
                    try:
                        self.connector._close()
                    except Exception :
                        pass
        except Exception :
            pass
    
    async def close(self):
        """Close the aiohttp session and connector"""
        async with self.rwlock.writer_lock:
            try:
                if self._session is not None and not self._session.closed:
                    try:
                        await self._session.close()
                    except Exception:
                        pass
            finally:
                self._session = None
                try:
                    if self.connector is not None and not self.connector.closed:
                        try:
                            await self.connector.close()
                        except Exception:
                            pass
                finally:
                    self.connector = None
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
    
    @staticmethod
    async def _build_rpc_request(text: str, dest: str, src: str):
        return json.dumps([[
            [
                RPC_ID,
                json.dumps([[text, src, dest, True], [None]], separators=(',', ':')),
                None,
                'generic',
            ],
        ]], separators=(',', ':'))

    async def _pick_service_url(self):
        if len(self.service_urls) == 1:
            return self.service_urls[0]
        return random.choice(self.service_urls)

    async def _translate_to_detect(self, text: str, dest: str, src: str):
        async with self._get_session() as session:
            host = await self._pick_service_url()
            url = urls.TRANSLATE_RPC.format(host=host.replace('googleapis', 'google'))
            data = {
                'f.req': await self._build_rpc_request(text, dest, src),
            }
            params = {
                'rpcids': RPC_ID,
                'bl': 'boq_translate-webserver_20201207.13_p0',
                'soc-app': 1,
                'soc-platform': 1,
                'soc-device': 1,
                'rt': 'c',
            }
            
            async with session.post(url, params=params, data=data, proxy=self.proxy, proxy_auth=self.proxy_auth, allow_redirects=True) as r:
                status = r.status
                text = await r.text()
                
                if status != 200 and self.raise_exception:
                    raise Exception('Unexpected status code "{}" from {}'.format(
                        status, self.service_urls))

                if "Our systems have detected unusual traffic from your computer network." in text:
                    raise RateLimitError

                return text, r

    async def _translate(self, text, dest, src, override):
        async with self._get_session() as session:
            token = '' #dummy default value here as it is not used by api client
            if self.client_type == 'webapp':
                token = await self.token_acquirer.do(text)

            params = await utils.build_params(client=self.client_type, query=text, src=src, dest=dest,
                                        token=token, override=override)

            url = urls.TRANSLATE.format(host=await self._pick_service_url())
            
            async with session.get(url, params=params, proxy=self.proxy, proxy_auth=self.proxy_auth) as r:
                status = r.status
                text_response = await r.text()
                
                if status == 200:
                    data = await utils.format_json(text_response)
                    return data, r

                if self.raise_exception:
                    raise Exception('Unexpected status code "{}" from {}'.format(
                        status, self.service_urls))

                DUMMY_DATA[0][0][0] = text
                return DUMMY_DATA, r

    def _parse_extra_data(self, data):
        response_parts_name_mapping = {
            0: 'translation',
            1: 'all-translations',
            2: 'original-language',
            5: 'possible-translations',
            6: 'confidence',
            7: 'possible-mistakes',
            8: 'language',
            11: 'synonyms',
            12: 'definitions',
            13: 'examples',
            14: 'see-also',
        }

        extra = {}

        for index, category in response_parts_name_mapping.items():
            extra[category] = data[index] if (
                index < len(data) and data[index]) else None

        return extra
    
    async def change_proxy(self, proxy: str = None, proxy_auth: tuple = None):
        """Change proxy during runtime

        :param proxy:  proxies configuration.
                        List mapping socks5 and http(s) host to the URL of the proxy
                        For example ``socks5://foo.bar:1080`` or ``https://foo.bar:8080``
        """
        async with self.rwlock.writer_lock:
            # Close existing session
            await self.close()
            
            connector = None
            self.proxy = None
            self.proxy_auth = None
            
            if proxy is not None:
                if proxy.startswith('socks5') or proxy.startswith('socks4'):
                    connector = ProxyConnector.from_url(proxy)
                else:
                    # HTTP/HTTPS proxy
                    self.proxy = proxy
                    self.proxy_auth = aiohttp.BasicAuth(proxy_auth[0], proxy_auth[1]) if proxy_auth else None
            
            if connector is None:
                connector = aiohttp.TCPConnector(limit=self.connector_limit)
            
            self.connector = connector
            
            # Reset session
            self._session = aiohttp.ClientSession(
                    connector=self.connector,
                    connector_owner=True,
                    timeout=self.timeout,
                    headers={'User-Agent': self.user_agent}
                )
            if self.client_type == 'webapp':
                self.token_acquirer = TokenAcquirer(
                    session=self._session, host=self.service_urls[0])

    async def translate(self, text, dest='en', src='auto', **kwargs):
        """Translate text from source language to destination language

        :param text: The source text(s) to be translated. Batch translation is supported via sequence input.
        :type text: UTF-8 :class:`str`; :class:`unicode`; string sequence (list, tuple, iterator, generator)

        :param dest: The language to translate the source text into.
                     The value should be one of the language codes listed in :const:`googletrans.LANGUAGES`
                     or one of the language names listed in :const:`googletrans.LANGCODES`.
        :param dest: :class:`str`; :class:`unicode`

        :param src: The language of the source text.
                    The value should be one of the language codes listed in :const:`googletrans.LANGUAGES`
                    or one of the language names listed in :const:`googletrans.LANGCODES`.
                    If a language is not specified,
                    the system will attempt to identify the source language automatically.
        :param src: :class:`str`; :class:`unicode`

        :rtype: Translated
        :rtype: :class:`list` (when a list is passed)

        Basic usage:
            >>> from googletrans import Translator
            >>> translator = Translator()
            >>> translator.translate('안녕하세요.')
            <Translated src=ko dest=en text=Good evening. pronunciation=Good evening.>
            >>> translator.translate('안녕하세요.', dest='ja')
            <Translated src=ko dest=ja text=こんにちは。 pronunciation=Kon'nichiwa.>
            >>> translator.translate('veritas lux mea', src='la')
            <Translated src=la dest=en text=The truth is my light pronunciation=The truth is my light>

        Advanced usage:
            >>> translations = translator.translate(['The quick brown fox', 'jumps over', 'the lazy dog'], dest='ko')
            >>> for translation in translations:
            ...    print(translation.origin, ' -> ', translation.text)
            The quick brown fox  ->  빠른 갈색 여우
            jumps over  ->  이상 점프
            the lazy dog  ->  게으른 개
        """
        src = src.lower().split('_', 1)[0]

        if src != 'auto' and src not in LANGUAGES:
            if src in SPECIAL_CASES:
                src = SPECIAL_CASES[src]
            elif src in LANGCODES:
                src = LANGCODES[src]
            else:
                raise ValueError('invalid source language')

        if dest not in LANGUAGES:
            if dest in SPECIAL_CASES:
                dest = SPECIAL_CASES[dest]
            elif dest in LANGCODES:
                dest = LANGCODES[dest]
            else:
                raise ValueError('invalid destination language')

        if isinstance(text, list):
            result = []
            for item in text:
                translated = await self.translate(item, dest=dest, src=src, **kwargs)
                result.append(translated)
            return result

        origin = text
        data, response = await self._translate(text, dest, src, kwargs)

        # this code will be updated when the format is changed.
        translated = ''.join([d[0] if d[0] else '' for d in data[0]])

        extra_data = self._parse_extra_data(data)

        # actual source language that will be recognized by Google Translator when the
        # src passed is equal to auto.
        try:
            temp_src = await self.translate_to_detect(text)
            src = temp_src.src#data[2]
        except RateLimitError:
            raise
        except Exception:  # pragma: nocover
            pass

        pron = origin
        try:
            pron = data[0][1][-2]
        except Exception:  # pragma: nocover
            pass

        if pron is None:
            try:
                pron = data[0][1][2]
            except Exception:  # pragma: nocover
                pass

        if dest in EXCLUDES and pron == origin:
            pron = translated

        # put final values into a new Translated object
        result = Translated(src=src, dest=dest, origin=origin,
                            text=translated, pronunciation=pron,
                            extra_data=extra_data,
                            response=response)

        return result

    async def translate_to_detect(self, text: str, dest='en', src='auto'):
        src = src.lower().split('_', 1)[0]

        if src != 'auto' and src not in LANGUAGES:
            if src in SPECIAL_CASES:
                src = SPECIAL_CASES[src]
            elif src in LANGCODES:
                src = LANGCODES[src]
            else:
                raise ValueError('invalid source language')

        if dest not in LANGUAGES:
            if dest in SPECIAL_CASES:
                dest = SPECIAL_CASES[dest]
            elif dest in LANGCODES:
                dest = LANGCODES[dest]
            else:
                raise ValueError('invalid destination language')

        origin = text
        data, response = await self._translate_to_detect(text, dest, src)

        token_found = False
        square_bracket_counts = [0, 0]
        resp = ''
        for line in data.split('\n'):
            token_found = token_found or f'"{RPC_ID}"' in line[:30]
            if not token_found:
                continue

            is_in_string = False
            for index, char in enumerate(line):
                if char == '\"' and line[max(0, index - 1)] != '\\':
                    is_in_string = not is_in_string
                if not is_in_string:
                    if char == '[':
                        square_bracket_counts[0] += 1
                    elif char == ']':
                        square_bracket_counts[1] += 1

            resp += line
            if square_bracket_counts[0] == square_bracket_counts[1]:
                break

        data = json.loads(resp)
        parsed = json.loads(data[0][2])
        # not sure
        should_spacing = parsed[1][0][0][3]
        translated_parts = list(map(lambda part: TranslatedPart(part[0], part[1] if len(part) >= 2 else []), parsed[1][0][0][5]))
        translated = (' ' if should_spacing else '').join(map(lambda part: part.text if part.text is not None else '', translated_parts))

        if src == 'auto':
            try:
                src = parsed[2]
            except Exception:
                pass
        if src == 'auto':
            try:
                src = parsed[0][2]
            except Exception:
                pass

        # currently not available
        confidence = None

        origin_pronunciation = None
        try:
            origin_pronunciation = parsed[0][0]
        except Exception:
            pass

        pronunciation = None
        try:
            pronunciation = parsed[1][0][0][1]
        except Exception:
            pass

        extra_data = {
            'confidence': confidence,
            'parts': translated_parts,
            'origin_pronunciation': origin_pronunciation,
            'parsed': parsed,
        }
        result = Translate_to_Detect(src=src, dest=dest, origin=origin,
                            text=translated, pronunciation=pronunciation,
                            parts=translated_parts,
                            extra_data=extra_data,
                            response=response)
        return result

    async def detect(self, text: str):
        translated = await self.translate_to_detect(text, src='auto', dest='en')
        result = Detected(lang=translated.src, confidence=translated.extra_data.get('confidence', None), response=translated._response)
        return result

    async def detect_legacy(self, text, **kwargs):
        """Detect language of the input text

        :param text: The source text(s) whose language you want to identify.
                     Batch detection is supported via sequence input.
        :type text: UTF-8 :class:`str`; :class:`unicode`; string sequence (list, tuple, iterator, generator)

        :rtype: Detected
        :rtype: :class:`list` (when a list is passed)

        Basic usage:
            >>> from googletrans import Translator
            >>> translator = Translator()
            >>> translator.detect('이 문장은 한글로 쓰여졌습니다.')
            <Detected lang=ko confidence=0.27041003>
            >>> translator.detect('この文章は日本語で書かれました。')
            <Detected lang=ja confidence=0.64889508>
            >>> translator.detect('This sentence is written in English.')
            <Detected lang=en confidence=0.22348526>
            >>> translator.detect('Tiu frazo estas skribita en Esperanto.')
            <Detected lang=eo confidence=0.10538048>

        Advanced usage:
            >>> langs = translator.detect(['한국어', '日本語', 'English', 'le français'])
            >>> for lang in langs:
            ...    print(lang.lang, lang.confidence)
            ko 1
            ja 0.92929292
            en 0.96954316
            fr 0.043500196
        """
        if isinstance(text, list):
            result = []
            for item in text:
                lang = await self.detect_legacy(item)
                result.append(lang)
            return result

        data, response = await self._translate(text, 'en', 'auto', kwargs)

        # actual source language that will be recognized by Google Translator when the
        # src passed is equal to auto.
        src = ''
        confidence = 0.0
        try:
            if len(data[8][0]) > 1:
                src = data[8][0]
                confidence = data[8][-2]
            else:
                src = ''.join(data[8][0])
                confidence = data[8][-2][0]
        except Exception:  # pragma: nocover
            pass
        result = Detected(lang=src, confidence=confidence, response=response)

        return result