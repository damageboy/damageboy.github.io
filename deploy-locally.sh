bundle exec jekyll build
export DEPLOY_BRANCH=master
export FOLDER=_site
git add -f _site/
git commit -m "Deploying to ${DEPLOY_BRANCH} - $(date +"%T")"
SUBTREE_REF=$(git subtree split --prefix $FOLDER)
git checkout $DEPLOY_BRANCH
git reset --hard ${SUBTREE_REF}
git clean -fdx
touch .nojekyll
git add -f .nojekyll
git commit -m "Disabling github pages builds - $(date +"%T")"
git push -f origin master
git clean -fdx
git checkout -
git reset --hard HEAD~1
