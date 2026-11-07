import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

test("同時に保持する2つのlocale画面でもlabel・入力・説明のIDが衝突しない", () => {
  // 通常のclient ReactでSSRする小さなフォームを使います。RSC条件で実行する親testと分離します。
  const script = `import assert from "node:assert/strict";
import React from "react"; import {renderToStaticMarkup} from "react-dom/server";
import {useFieldIds} from "./lib/use-field-ids.ts";
const fields=[];
function Form({label}) { const id=useFieldIds(), field=id("owner-group"), help=id("help"); fields.push({field,help}); return React.createElement("form",{},React.createElement("label",{htmlFor:field},label),React.createElement("button",{id:field,role:"combobox","aria-describedby":help},"value"),React.createElement("p",{id:help},"help")); }
const html=renderToStaticMarkup(React.createElement(React.Fragment,{},React.createElement(Form,{label:"所有グループ"}),React.createElement(Form,{label:"Owner groups"})));
const ids=[...html.matchAll(/ id="([^"]+)"/g)].map(x=>x[1]); assert.equal(new Set(ids).size,ids.length); assert.equal(fields.length,2); assert.notEqual(fields[0].field,fields[1].field);
for(const {field,help}of fields) { assert.equal(html.split('for="'+field+'"').length-1,1); assert.equal(html.split('aria-describedby="'+help+'"').length-1,1); }
`;
  const result = spawnSync(process.execPath, ["--experimental-strip-types", "--input-type=module", "-e", script], { cwd: fileURLToPath(new URL("..", import.meta.url)), encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
});
