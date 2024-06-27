This repository hosts GitHub Actions developed by the ASF community and approved for any ASF top level project to use.

### Submitting an Action
  - Create a branch of the repository
  - In the branch, create a subdirectory for your proposed GHA (at the top level, select 'add file' and provide `/NameOfAction`)
  - In the subdirectory, add all the required files for your proposed GHA. Make sure to add a README file that says what the Action does, and any particular configurations or considerations a user would find helpful.
  - Create a pull request to merge the branch you created into the trunk of the repository.

Infra will review each proposeed Action for usefulness to the community and an estimate of difficulty of maintenance. Infra may raise questions in pull request comments.

Once everything seems in order, Infra will approve the pull request and add the new Action to the list of available Actions.

### GH Actions available

  - [ASF Infrastructure Pelican Action](/pelican/README.txt)
