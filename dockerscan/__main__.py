def main():
    import os
    import sys 
    import pdb



    if sys.version_info < (3, 5,):
        print("To run dockerscan you Python 3.5+")
        sys.exit(0)
    pdb.set_trace()
    parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(1, parent_dir) #sys.path模块是动态的修改系统路径,将其添加到路径中可以很方便导入该路径下的模块
    import dockerscan

    __package__ = str("dockerscan")

    # Run the cmd
    from dockerscan.actions.cli import cli

    cli()

if __name__ == "__main__":  # pragma no cover
    main()
