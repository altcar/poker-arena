import { spawn } from "node:child_process";
import { setTimeout as sleep } from "node:timers/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
const EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const PORT = Number(process.env.PORT || (9400 + Math.floor(Math.random() * 400)));
const BASE = "https://poker-arena.hheylau.workers.dev";
const profile = join(tmpdir(), `poker-forms-${Date.now()}`);
const edge = spawn(EDGE, ["--headless=new","--disable-gpu","--no-sandbox",`--remote-debugging-port=${PORT}`,`--user-data-dir=${profile}`,`${BASE}/#/login`], { stdio: "ignore" });
process.on("exit", () => { try { spawn("taskkill", ["/PID", String(edge.pid), "/T", "/F"], { stdio: "ignore" }); } catch { /* ignore */ } });
let target;
for (let i=0;i<60;i++){try{const l=await(await fetch(`http://127.0.0.1:${PORT}/json`)).json();target=l.find(x=>x.type==="page"&&x.url.includes("poker-arena"));if(target)break;}catch{}await sleep(500);}
const ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise(r=>ws.addEventListener("open",r,{once:true}));
let id=1;function ev(e){const i=id++;return new Promise(res=>{const hnd=(m)=>{const x=JSON.parse(m.data);if(x.id!==i)return;ws.removeEventListener("message",hnd);res(x.result?.result?.value)};ws.addEventListener("message",hnd);ws.send(JSON.stringify({id:i,method:"Runtime.evaluate",params:{expression:e,returnByValue:true,awaitPromise:true}}))})}
async function waitFor(text,seconds=30){for(let i=0;i<seconds*2;i++){const t=await ev("document.body.textContent");if(typeof t==="string"&&t.includes(text))return true;await sleep(500)}return false}
async function click(text){return ev(`(() => { const b=[...document.querySelectorAll("button")].find((n)=>n.textContent.trim().includes(${JSON.stringify(text)})); if(!b)return false; b.click(); return true; })()`)}
async function check(name,ok,detail=""){console.log(`${ok?"PASS":"FAIL"} ${name}${detail?` — ${detail}`:""}`);if(!ok)process.exitCode=1}
async function go(hash){await ev(`location.hash=${JSON.stringify(hash)}`);await sleep(500)}

await waitFor("Welcome back",30);
await ev(`(() => { const inputs=[...document.querySelectorAll("input")]; inputs.find(i=>i.type==="email").value="demo@poker-arena.local"; inputs.find(i=>i.type==="password").value="arena-demo-2026"; [...document.querySelectorAll("button")].find(b=>b.textContent.includes("Log in")).click(); })()`);
check("login", await waitFor("Upload a bot",30));

await go("#/upload");
check("upload form", await waitFor("Upload a bot",20));
await ev(`(() => {
  const name=[...document.querySelectorAll("input")].find(i=>i.placeholder==="My first bot");
  name.value="UITestBot";
  const ta=document.querySelector("textarea");
  ta.value=ta.value.replace("A minimal, legal bot.", "Uploaded through the form test.");
})()`);
await click("Upload bot");
check("upload posted", await waitFor("UITestBot",40));

await go("#/tournaments/season-2-kickoff");
check("tournament entry form", await waitFor("Enter with your bot",20));
await ev(`(() => { const s=document.querySelector("select"); const o=[...s.options].find(o=>o.textContent.includes("UITestBot")); if(o) s.value=o.value; })()`);
await click("Enter tournament");
check("tournament entered", await waitFor("Entered",20));

await go("#/newsletter");
check("newsletter form", await waitFor("Subscribe",20));
await ev(`(() => { const i=document.querySelector("input[name=email]"); i.value="formtest@example.com"; })()`);
await click("Subscribe");
check("newsletter subscribed", await waitFor("on the list",20));

await go("#/forum/new");
check("new thread form", await waitFor("Post thread",20));
await ev(`(() => {
  const inputs=[...document.querySelectorAll("input")];
  const title=inputs.find(i=>i.placeholder==="Thread title");
  title.value="Automated form test thread";
  document.querySelector("textarea").value="This thread was posted by the browser test. It should be removed afterwards.";
})()`);
await click("Post thread");
check("thread posted", await waitFor("Automated form test thread",30));
check("no errors", (await ev("window.__errs ? window.__errs.length : 0"))===0, await ev("JSON.stringify(window.__errs||[])"));

ws.close();
spawn("taskkill", ["/PID", String(edge.pid), "/T", "/F"], { stdio: "ignore" });
