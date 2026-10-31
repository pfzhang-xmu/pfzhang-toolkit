// Derived from evi0s/WMPFDebugger (GPL-2.0-only).
// XMU changes: scoped app, protocol scene 1023, retained callbacks, cleanup.
const retained=[];const changes=[];let restored=false,moduleObserver=null;
const readable = (p,size) => {
    if(p.isNull())return false;
    const range=Process.findRangeByAddress(p);
    return !!range&&range.protection.includes('r')&&p.add(size).compare(range.base.add(range.size))<=0;
};
const ownsApp = p => {
    if(p.isNull())return false;
    const range=Process.findRangeByAddress(p);
    if(!range||!range.protection.includes('r'))return false;
    const size=Math.min(2048,Number(range.base.add(range.size).sub(p)));
    return Array.from(new Uint8Array(p.readByteArray(size)),x=>String.fromCharCode(x)).join('').includes('wx81a2b2fa90759cb7');
};
const getPlatform = () => {
    // retval: "windows" | "linux" | "darwin"
    return Process.platform;
}

const getMainModule = (version) => {
    const osPlatform = getPlatform();
    if (osPlatform === 'windows') {
        if (version >= 13331) {
            return Process.findModuleByName("flue.dll");
        }
        return Process.findModuleByName("WeChatAppEx.exe");
    } else if (osPlatform === 'linux') {
        return Process.findModuleByName("WeChatAppEx");
    } else if (osPlatform === 'darwin') {
        return Process.findModuleByName("WeChatAppEx Framework");
    }
};

const patchCDPFilter = (base, config) => {
    // xref: SendToClientFilter OR devtools_message_filter_applet_webview.cc
    // xref: CastToJson
    if (config.CastToJsonHookOffset) {
        // credit: @Redbeanw44602, pr #262
        const osPlatform = getPlatform();
        if (osPlatform === "windows" || osPlatform === "darwin") {
            // TODO: this was not tested on darwin
            const castToJsonFunc = new NativeFunction(
                base.add(config.CastToJsonHookOffset),
                "pointer",
                ["pointer", "pointer"]
            );
            const callback = new NativeCallback(function(thiz, jsonOut, cborInput) {
                castToJsonFunc(jsonOut, cborInput);
                return jsonOut;
            }, "pointer", ["pointer", "pointer", "pointer"]);
            retained.push(castToJsonFunc,callback);
            Interceptor.replace(base.add(config.CDPFilterHookOffset), callback);
        }
        if (osPlatform === "linux") {
            const castToJsonFunc = new NativeFunction(
                base.add(config.CastToJsonHookOffset),
                "pointer",
                ["pointer", "pointer", "pointer"]
            );
            const callback = new NativeCallback(function (jsonOut, thiz, cborInput, cborLen) {
                castToJsonFunc(jsonOut, cborInput, cborLen)
                return jsonOut
            }, "pointer", ["pointer", "pointer", "pointer", "pointer"]);
            retained.push(castToJsonFunc,callback);
            Interceptor.replace(base.add(config.CDPFilterHookOffset), callback);
        }
        return;
    }

    // legacy flue fallback
    const offset = config.CDPFilterHookOffset;
    Interceptor.attach(base.add(offset), {
        onLeave(retval_) {
            // see https://github.com/evi0s/WMPFDebugger/pull/262
            try {
                if(getPlatform()==='windows'&&!readable(retval_,Process.pointerSize))return;
                const retval=getPlatform()==='windows'?retval_.readPointer():retval_;
                if(!readable(retval,12))return;
                const val = retval.add(8).readU32();
                send(`[patch] CDP filter on leave, retval+8 = ${val}`);
                if (val === 6) {
                    retval.add(8).writeU32(0x0);
                    send("[patch] CDP filter patched");
                }
            } catch (e) {
                send(`[patch] CDP filter error: ${e}`);
            }
        }
    });
};

const handleOnLoadStart = (a1, config) => {
    let miniappLaunchConfigPtr;
    let remoteDebugConfigPtr;
    let miniappScenePtr;

    const structOffsets = config.MiniAppConfigStructOffsets
        ? config.MiniAppConfigStructOffsets
        : config.SceneOffsets;

    // legacy scene config
    if (config.SceneOffsets) {
        miniappLaunchConfigPtr = a1
            .add(structOffsets[0])
            .readPointer()
            .add(structOffsets[1])
            .readPointer()
            .add(structOffsets[2])
            .readPointer();
        remoteDebugConfigPtr = miniappLaunchConfigPtr
            .add(structOffsets[3])
            .readPointer()
            .add(structOffsets[4])
            .readPointer();

        miniappScenePtr = remoteDebugConfigPtr.add(structOffsets[5]);
    } else {
        // later wmpf builds (win32)
        const launchConfigOffsets = structOffsets.LaunchConfigOffsets;
        const remoteDebugConfigOffsets = structOffsets.RemoteDebugConfigOffsets;
        miniappLaunchConfigPtr = a1
            .add(launchConfigOffsets[0])
            .readPointer()
            .add(launchConfigOffsets[1])
            .readPointer()
            .add(launchConfigOffsets[2])
            .readPointer();
        remoteDebugConfigPtr = miniappLaunchConfigPtr
            .add(remoteDebugConfigOffsets[0])
            .readPointer()
            .add(remoteDebugConfigOffsets[1])
            .readPointer();

        miniappScenePtr = remoteDebugConfigPtr.add(structOffsets.SceneOffset);
    }

    // 1000: from issue #83 <-- will crash the process
    // 1007: from issue #80
    // 1008: from issue #53
    // 1011: scan QR code
    // 1012: recognize QR code from long-pressed image (issue #128)
    // 1027: from issue #78
    // 1035: from issue #78
    // 1037: opened from another mini program
    // 1053: from issue #25
    // 1074: from issue #32
    // 1145: from search
    // 1178: from phone (issue #117)
    // 1256: from recent
    // 1260: from frequently used
    // 1302: from services
    // 1308: minigame?
    const sceneNumberArray = [
        1005, 1007, 1008, 1011, 1012, 1027, 1035, 1037, 1053, 1074, 1145, 1178,
        1256, 1260, 1302, 1308,
    ];
    let ownApp = false;
    for (const candidate of [a1, miniappLaunchConfigPtr, remoteDebugConfigPtr]) {
        try {
            if (ownsApp(candidate)) ownApp = true;
        } catch (_) {}
    }
    if(!ownApp)return false;
    // Desktop protocol scene 1023 is used by XMU on this build. Keep preload
    // scene 1000 untouched; only the verified app receives the extra branch.
    if (!sceneNumberArray.includes(miniappScenePtr.readInt()) && ![1023,1101].includes(miniappScenePtr.readInt())) {
        return;
    }

    send(`[hook] scene: ${miniappScenePtr.readInt()}`);
    send("[hook] hook scene condition -> 1101");
    // Re-entering retires the previous app container. Do not retain its pointers.
    changes.length=0;
    changes.push({scene:miniappScenePtr,launch:miniappLaunchConfigPtr,owner:a1,original:miniappScenePtr.readInt()===1101?1023:miniappScenePtr.readInt()});
    miniappScenePtr.writeInt(1101);

    if (config.SceneOffsets) {
        // legacy path, we are done here
        return true;
    }

    // setup the websocket back connection URL for new flue builds
    // it's now adjustable as well :)
    const websocketUrl = "ws://localhost:9421";
    const websocketUrlStringPtr = miniappLaunchConfigPtr
            .add(structOffsets.WebSocketURLStringOffset);
    const stringMarker = websocketUrlStringPtr.add(23).readS8();
    if (stringMarker < 0) {
        // long representation: { data pointer, length, capacity | high bit }.
        websocketUrlStringPtr.readPointer().writeUtf8String(websocketUrl);
        websocketUrlStringPtr.add(8).writeU64(websocketUrl.length);
    } else {
        // short representation: 23 inline bytes followed by a one-byte length.
        websocketUrlStringPtr.writeUtf8String(websocketUrl);
        websocketUrlStringPtr.add(23).writeU8(websocketUrl.length);
    }
    send(`[hook] websocket url -> ${websocketUrl}`);

    const remoteDebugModePtr = remoteDebugConfigPtr
            .add(structOffsets.RemoteDebugModeOffset);
    send(`[hook] remote debug mode: ${remoteDebugModePtr.readInt()} -> 1`);
    remoteDebugModePtr.writeInt(1);
};

const patchOnLoadStart = (base, config) => {
    // xref: AppletIndexContainer::OnLoadStart
    Interceptor.attach(base.add(config.LoadStartHookOffset), {
        onEnter(args) {
            send(
                `[inteceptor] AppletIndexContainer::OnLoadStart onEnter, ` +
                `indexContainer.this: ${args[0]}`,
            );
            if(handleOnLoadStart(args[0],config) && args[1].and(0xff).toInt32() !== 1) {
                args[1] = args[1].and(ptr("0xffffffffffffff00")).or(1);
            }
        },
        onLeave(retval) {
            // do nothing
        },
    });
};

const parseConfig = () => {
    const rawConfig = `@@CONFIG@@`;
    if (rawConfig.includes("@@")) {
        // test addresses
        return {
            Version: 18955,
            LoadStartHookOffset: "0x25B52C0",
            CDPFilterHookOffset: "0x30248B0",
            SceneOffsets: [1408, 1344, 488],
        };
    }
    return JSON.parse(rawConfig);
};

const main = () => {
    const config = parseConfig();
    const mainModule = getMainModule(config.Version);
    if (!mainModule) {
        moduleObserver=Process.attachModuleObserver({onAdded(module) {
            if (module.name === "flue.dll") {
                patchOnLoadStart(module.base, config);
                patchCDPFilter(module.base, config);
                send("[hook] flue loaded and hooks installed");
            }
        }});
        return;
    }
    patchOnLoadStart(mainModule.base, config);
    patchCDPFilter(mainModule.base, config);
};

rpc.exports={restore(){
    if(restored)return true;
    restored=true;
    if(moduleObserver)moduleObserver.detach();
    Interceptor.detachAll();
    for(const item of changes){try{
        const own=[item.owner,item.launch].some(p=>{try{return ownsApp(p);}catch(_){return false;}});
        const range=Process.findRangeByAddress(item.scene);
        if(own&&readable(item.scene,4)&&range.protection.includes('w')&&item.scene.readInt()===1101)item.scene.writeInt(item.original);
    }catch(_){}}
    changes.length=0;
    const module=getMainModule(parseConfig().Version);
    if(module)try{Interceptor.revert(module.base.add(parseConfig().CDPFilterHookOffset));}catch(_){}
    return true;
}};
main();
