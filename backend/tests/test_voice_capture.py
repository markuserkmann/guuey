import array
import math
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import threading
import wave

import pytest
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / 'frontend'


@pytest.fixture(scope='module')
def voice_browser(tmp_path_factory):
    directory = tmp_path_factory.mktemp('voice-browser')
    esbuild = FRONTEND / 'node_modules/.bin/esbuild'
    if not esbuild.exists():
        pytest.skip('Install frontend dependencies to run browser microphone checks')
    bundle = directory / 'voice.mjs'
    subprocess.run([str(esbuild), '--bundle', '--format=esm', '--loader=tsx', '--jsx=automatic', f'--outfile={bundle}'],
        cwd=FRONTEND, check=True, capture_output=True, text=True, input='''
        import React, {useState} from 'react';
        import {createRoot} from 'react-dom/client';
        import {VoiceTranscription} from './src/voiceTranscription';
        import {Composer} from './src/components/Composer';
        window.VoiceTranscription = VoiceTranscription;
        function Harness() {
            const [draft, setDraft] = useState('');
            return <Composer token={'a'.repeat(40)} draft={draft} onDraftChange={setDraft}
                attachments={[]} onAttachmentsChange={()=>{}} mode="auto" onModeChange={()=>{}}
                forceBrowser={false} onForceBrowserChange={()=>{}} browserAvailable={true}
                onSubmit={event=>{event.preventDefault();window.submissions.push(draft);}}
                disabled={false} busy={false} deepAvailable={true}/>;
        }
        window.renderComposer = () => createRoot(document.querySelector('#app')).render(<Harness/>);
        ''')
    audio = directory / 'microphone.wav'
    with wave.open(str(audio), 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(48000)
        output.writeframes(array.array('h', (int(8000 * math.sin(2 * math.pi * 400 * i / 48000))
                                            for i in range(48000 * 5))).tobytes())
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            if self.path == '/voice.mjs':
                content, mime = bundle.read_bytes(), 'text/javascript'
            elif self.path == '/audioCapture.worklet.js':
                content = (FRONTEND / 'src/audioCapture.worklet.js').read_bytes()
                mime = 'text/javascript'
            else:
                content = (b'<div id="app"></div><button id="start">Record</button>'
                           b'<script type="module" src="/voice.mjs"></script>')
                mime = 'text/html'
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.end_headers()
            self.wfile.write(content)

    with sync_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).exists():
            pytest.skip('Install Chromium to run browser microphone checks')
        browser = playwright.chromium.launch(args=[
            '--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream',
            f'--use-file-for-fake-audio-capture={audio}',
        ])
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            yield browser, f'http://127.0.0.1:{server.server_port}'
        finally:
            browser.close()
            server.shutdown()
            server.server_close()


@pytest.fixture
def voice_page(voice_browser):
    browser, url = voice_browser
    context = browser.new_context()
    page = context.new_page()
    page.set_default_timeout(5000)
    page.goto(url)
    page.wait_for_function('Boolean(window.VoiceTranscription)')
    page.evaluate('''() => {
        window.audioFrames=[];window.events=[];window.submissions=[];window.delay=0;
        const capture = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
        navigator.mediaDevices.getUserMedia=async options=>{
            const stream=await capture(options);window.stream=stream;
            window.captureStarted=performance.now();return stream;
        };
        window.fetch=async(path, options)=>{
            window.tokenRequest={path,headers:options.headers};
            return Response.json({session:{
                token:'auth_tokens/test',model:'gemini-3.5-transcribe-live',
                apiVersion:'v1alpha',responseModalities:['TEXT'],
            }});
        };
        class Socket {
            static OPEN=1;readyState=0;
            constructor(url){window.socket=this;window.socketUrl=url;setTimeout(()=>{this.readyState=1;this.onopen?.();},0);}
            deliver(message){this.onmessage?.({data:JSON.stringify(message)});}
            send(data){
                const message=JSON.parse(data);
                if(message.setup)setTimeout(()=>{
                    this.deliver({setupComplete:{}});window.providerReady=performance.now();
                },window.delay);
                const input=message.realtimeInput;
                if(input?.activityStart)window.events.push('activityStart');
                if(input?.audio){
                    window.events.push('audio');
                    window.audioFrames.push(Uint8Array.from(atob(input.audio.data),character=>character.charCodeAt(0)));
                }
                if(input?.activityEnd){window.events.push('activityEnd');window.stopSent=true;window.stoppedAt=performance.now();}
            }
            close(){this.readyState=3;}
        }
        window.WebSocket=Socket;
        window.begin=()=>{
            window.voice=new VoiceTranscription('a'.repeat(40),{
                onReady:()=>window.events.push('ready'),
                onStopping:()=>window.events.push('stopping'),
                onTranscript:()=>{},onEnd:error=>{window.error=error;window.done=true;},
            });
            return window.voice.start();
        };
        document.querySelector('#start').onclick=window.begin;
    }''')
    try:
        yield page
    finally:
        context.close()


def test_browser_keeps_audio_while_provider_connects_and_releases_microphone(voice_page):
    page = voice_page
    page.evaluate('window.delay=1200')
    page.click('#start')
    page.wait_for_function('window.events.includes("ready")')
    page.evaluate('window.voice.stop()')
    page.wait_for_function('window.stopSent')
    result = page.evaluate('''() => ({
        seconds:window.audioFrames.reduce((n,frame)=>n+frame.length,0)/32000,
        elapsed:(window.stoppedAt-window.captureStarted)/1000,
        microphoneOff:window.stream.getTracks().every(track=>track.readyState==='ended'),
        sampleRate:window.voice.context.sampleRate,
    })''')
    assert result['seconds'] > 0.95, result
    assert abs(result['seconds'] - result['elapsed']) < 0.2, result
    assert result['sampleRate'] in (44100, 48000)
    assert page.evaluate('window.events[0]') == 'activityStart'
    assert page.evaluate('window.events.at(-1)') == 'activityEnd'
    assert '/api/voice/token' == page.evaluate('window.tokenRequest.path')
    assert 'generativelanguage.googleapis.com' in page.evaluate('window.socketUrl')
    assert 'access_token=auth_tokens' in page.evaluate('window.socketUrl')
    assert result['microphoneOff']
    page.evaluate('window.socket.deliver({serverContent:{generationComplete:true}})')
    page.wait_for_function('window.done')
    assert page.evaluate('window.done && !window.error')


def test_cancelling_connection_discards_buffer_and_stops_capture(voice_page):
    page = voice_page
    page.evaluate('window.delay=5000')
    page.click('#start')
    page.wait_for_function('window.voice.pendingSize > 0')
    page.evaluate('window.voice.stop()')
    assert page.evaluate('window.stream.getTracks().every(track=>track.readyState==="ended")')
    assert page.evaluate('window.voice.pendingSize === 0 && window.voice.pending.length === 0')
    assert page.evaluate('window.audioFrames.length === 0')


def test_send_waits_for_final_text_instead_of_submitting_the_preview(voice_page):
    page = voice_page
    page.evaluate('window.renderComposer()')
    page.get_by_role('button', name='Start voice input').click()
    page.wait_for_function('Boolean(window.providerReady)')
    page.evaluate('window.socket.deliver({serverContent:{interimInputTranscription:{text:"a private song"}}})')
    page.get_by_role('button', name='Stop voice input').click()
    page.wait_for_function('window.stopSent')
    assert page.get_by_role('button', name='Send message').is_disabled()
    assert not page.get_by_role('textbox', name='Message').get_attribute('readonly')
    page.get_by_role('textbox', name='Message').press('Enter')
    assert page.evaluate('window.submissions') == []
    page.evaluate('''() => {
        window.socket.deliver({serverContent:{inputTranscription:{text:'a private sauna'}}});
        window.socket.deliver({serverContent:{generationComplete:true}});
    }''')
    page.get_by_role('button', name='Send message').click()
    assert page.evaluate('window.submissions') == ['a private sauna']


def test_editing_after_stop_preserves_the_users_correction(voice_page):
    page = voice_page
    page.evaluate('window.renderComposer()')
    page.get_by_role('button', name='Start voice input').click()
    page.wait_for_function('Boolean(window.providerReady)')
    page.evaluate('window.socket.deliver({serverContent:{interimInputTranscription:{text:"preview"}}})')
    page.get_by_role('button', name='Stop voice input').click()
    page.wait_for_function('window.stopSent')
    page.get_by_role('textbox', name='Message').fill('My corrected words')
    page.evaluate('window.socket.deliver({serverContent:{inputTranscription:{text:"late transcript"}}})')
    page.get_by_role('button', name='Send message').click()
    assert page.evaluate('window.submissions') == ['My corrected words']


def test_transcript_fragments_join_without_inserting_spaces_inside_words(voice_page):
    page = voice_page
    page.evaluate('window.renderComposer()')
    page.get_by_role('button', name='Start voice input').click()
    page.wait_for_function('Boolean(window.providerReady)')
    page.evaluate("""() => {
        window.socket.deliver({serverContent:{inputTranscription:{text:'Find a wheel'}}});
        window.socket.deliver({serverContent:{inputTranscription:{text:'chair-accessible sauna.'}}});
        window.socket.deliver({serverContent:{interimInputTranscription:{text:'Check parking'}}});
        window.socket.deliver({serverContent:{interimInputTranscription:{text:'Check prices'}}});
    }""")
    textbox = page.get_by_role('textbox', name='Message')
    assert textbox.input_value() == 'Find a wheelchair-accessible sauna. Check prices'
    page.get_by_role('button', name='Stop voice input').click()
    page.wait_for_function('window.stopSent')
    page.evaluate("""() => {
        window.socket.deliver({serverContent:{generationComplete:true}});
        setTimeout(()=>window.socket.deliver({serverContent:{inputTranscription:{text:' Check prices.'}}}),100);
    }""")
    page.get_by_role('button', name='Send message').click()
    assert page.evaluate('window.submissions') == ['Find a wheelchair-accessible sauna. Check prices.']
