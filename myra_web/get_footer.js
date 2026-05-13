import fs from 'fs';

async function fetchFile() {
  const res = await fetch('https://raw.githubusercontent.com/Nirav846/myra/main/myra_app/UI_Manager.py');
  const text = await res.text();
  const lines = text.split('\n');
  
  console.log(lines.slice(360, 450).join('\n'));
}
fetchFile();
