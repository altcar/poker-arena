import { spawn } from "node:child_process";
import { setTimeout as sleep } from "node:timers/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
const EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const PORT = Number(process.env.PORT || (9400 + Math.floor(Math.random() * 400)));
const BASE = "https://poker-arena.hheylau.workers.dev";
const profile = join(tmpdir(), `poker-modes-${Date.now()}`);
const edge = spawn(EDGE, ["--headless=new","--disable-gpu","--no-sandbox",`--remote-debugging-port=${PORT}`,`--user-data-dir=${profile}`,`${BASE}/#/play?mode=boss`], { stdio: "ignore" });
process.on("exit", () => { try { spawn("taskkill", ["/PID", String(edge.pid), "/T", "/F"], { stdio: "ignore" }); } catch { /* ignore */ } });
let target;
for (let i=0;i<60;i++){try{const l=await(await fetch(`http://127.0.0.1:${PORT}/json`)).json();target=l.find(x=>x.type==="page"&&x.url.includes("poker-arena"));if(target)break;}catch{}await sleep(500);}
const ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise(r=>ws.addEventListener("open",r,{once:true}));
let id=1;function ev(e){const i=id++;return new Promise(res=>{const hnd=(m)=>{const x=JSON.parse(m.data);if(x.id!==i)return;ws.removeEventListener("message",hnd);res(x.result?.result?.value)};ws.addEventListener("message",hnd);ws.send(JSON.stringify({id:i,method:"Runtime.evaluate",params:{expression:e,returnByValue:true,awaitPromise:true}}))})}
async function waitFor(text,seconds=30){for(let i=0;i<seconds*2;i++){const t=await ev("document.body.textContent");if(typeof t==="string"&&t.includes(text))return true;await sleep(500)}return false}
async function click(text){return ev(`(() => { const b=[...document.querySelectorAll("button")].find((n)=>n.textContent.trim().includes(${JSON.stringify(text)})); if(!b)return false; b.click(); return true; })()`)}
async function check(name,ok,detail=""){console.log(`${ok?"PASS":"FAIL"} ${name}${detail?` — ${detail}`:""}`);if(!ok)process.exitCode=1}

await waitFor("Start the run",30);
await click("Start the run");
check("boss table", await waitFor("The Bully",20));
let played=0;
for(let i=0;i<20;i++){
  const t=await ev("document.body.textContent");
  if(t.includes("Face The Wall")||t.includes("Run complete")||t.includes("Boss rules")){break}
  if(t.includes("Deal next hand")){await click("Deal next hand");played++;await sleep(250);continue}
  if(t.includes("Check")){await click("Check");await sleep(250);continue}
  if(t.includes("Call ")){await click("Call ");await sleep(250);continue}
  if(t.includes("Fold")){await click("Fold");await sleep(250);continue}
  await sleep(250);
}
check("boss hands advanced", played>0 || (await ev("document.body.textContent")).includes("Hand 2"), "played="+played);

await ev(`location.hash = "#/play?mode=hu&opponent=aggressive"`);
check("hu js challenge", await waitFor("Aggressive",40));
check("hu action", await click("Check")||await click("Call ")||await click("Fold"));

await ev(`location.hash = "#/play?mode=hu&opponent=relief-grid"`);
check("hu python challenge", await waitFor("relief-grid",120));
check("hu python action", await click("Check")||await click("Call ")||await click("Fold"));
check("no errors", (await ev("window.__errs ? window.__errs.length : 0"))===0, await ev("JSON.stringify(window.__errs||[])"));

ws.close();
spawn("taskkill", ["/PID", String(edge.pid), "/T", "/F"], { stdio: "ignore" });
