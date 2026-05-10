import * as fs from "fs";
import * as path from "path";

function processDir(dir: string) {
  const files = fs.readdirSync(dir);
  for (const file of files) {
    const fullPath = path.join(dir, file);
    if (fs.statSync(fullPath).isDirectory()) {
      processDir(fullPath);
    } else if (fullPath.endsWith(".tsx") || fullPath.endsWith(".ts")) {
      let content = fs.readFileSync(fullPath, "utf-8");
      let modified = false;

      // Add native option styling if missing
      const oldContent = content;
      content = content.replace(
        /<option(?!\s+[^>]*?className)([^>]*)>/g,
        '<option className="bg-[#1a1c24] text-[#fafafa]"$1>',
      );

      if (content !== oldContent) {
        fs.writeFileSync(fullPath, content, "utf-8");
        console.log("Updated", fullPath);
      }
    }
  }
}

processDir(path.join(process.cwd(), "src", "views"));
