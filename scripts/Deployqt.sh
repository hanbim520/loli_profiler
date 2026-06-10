set -xeuo pipefail
echo Deployqt.....
pwd
pushd .
cd $ReleasePath
cd ../
$MacdeployqtPath LoliProfiler.app -dmg -always-overwrite

popd

mkdir $DeployPath

cp $ReleasePath/../LoliProfiler.dmg $DeployPath

echo Copying Python analysis scripts...
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp "$SCRIPT_DIR/../markdown_to_html.py" "$DeployPath/"
cp "$SCRIPT_DIR/../analyze_heap.py" "$DeployPath/"
cp "$SCRIPT_DIR/../pyproject.toml" "$DeployPath/"

echo Copying loli CLI files...
mkdir -p "$DeployPath/loli_cli"
cp "$SCRIPT_DIR/../loli_cli/__init__.py" "$DeployPath/loli_cli/"
cp "$SCRIPT_DIR/../loli_cli/tree_model.py" "$DeployPath/loli_cli/"
cp "$SCRIPT_DIR/../loli_cli/loli_convert.py" "$DeployPath/loli_cli/"
cp "$SCRIPT_DIR/../loli_cli/core.py" "$DeployPath/loli_cli/"
cp "$SCRIPT_DIR/../loli_cli/cli.py" "$DeployPath/loli_cli/"
cp "$SCRIPT_DIR/../loli_cli/README.md" "$DeployPath/loli_cli/"

echo finish Deployqt.....