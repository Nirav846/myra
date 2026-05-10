const fs = require("fs");
const path = require("path");

function processDir(dir) {
  const files = fs.readdirSync(dir);
  for (const file of files) {
    const fullPath = path.join(dir, file);
    if (fs.statSync(fullPath).isDirectory()) {
      processDir(fullPath);
    } else if (fullPath.endsWith(".tsx") || fullPath.endsWith(".ts")) {
      let content = fs.readFileSync(fullPath, "utf-8");
      let modified = false;

      // Add native option styling if missing
      content = content.replace(
        /<select([^>]*?)className=(["'])(.*?)\2/g,
        (match, prefix, quote, classNames) => {
          if (!classNames.includes("[&>option]:bg-[")) {
            modified = true;
            return `<select${prefix}className=${quote}${classNames} [&>option]:bg-[#1a1c24] [&>option]:text-[#fafafa]${quote}`;
          }
          return match;
        },
      );

      if (modified) {
        fs.writeFileSync(fullPath, content, "utf-8");
        console.log("Updated", fullPath);
      }
    }
  }
}

processDir(path.join(__dirname, "src", "views"));
processDir(path.join(__dirname, "src", "components"));
